"""SQLite persistence (SPEC §6): run state, append-only event journal and approvals, LLM cache, metrics."""

import json
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple, Self

import aiosqlite
from pydantic import BaseModel

from src.models.state import AgentProcessState, ApprovalDecision, ProcessStatus, StepMetrics

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    step TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_run ON events (run_id, id);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    gate TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    comment TEXT,
    decided_at TEXT NOT NULL,
    iteration INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    run_id TEXT NOT NULL,
    step TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    prompt_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    PRIMARY KEY (run_id, step, iteration, prompt_hash)
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    model_id TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    compute_ms REAL NOT NULL,
    wait_ms REAL NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    status TEXT NOT NULL
);
"""

APPEND_ONLY_TABLES = ("events", "approvals")
APPEND_ONLY_TRIGGERS = "".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_{op} BEFORE {op.upper()} ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"""
    for table in APPEND_ONLY_TABLES
    for op in ("update", "delete")
)


class EventRecord(BaseModel):
    id: int
    run_id: str
    ts: datetime
    step: str
    iteration: int
    event_type: str
    payload: dict[str, Any]


class CachedLLMResponse(NamedTuple):
    response_json: str
    usage_json: str


class SqliteStore:
    """Async store over a single aiosqlite connection. Use as `async with SqliteStore(path) as store:`."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA + APPEND_ONLY_TRIGGERS)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Databases created before the plan gate have no `approvals.gate`; their decisions were writes."""
        columns = {row["name"] for row in await self._fetch("PRAGMA table_info(approvals)")}
        if "gate" not in columns:
            await self.db.execute("ALTER TABLE approvals ADD COLUMN gate TEXT NOT NULL DEFAULT 'write'")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.close()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SqliteStore is not open; use 'async with SqliteStore(path)'")
        return self._db

    async def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        await self.db.execute(sql, params)
        await self.db.commit()

    async def _fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        async with self.db.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    # --- runs -------------------------------------------------------------------------------------------

    async def save_state(self, state: AgentProcessState, updated_at: datetime) -> None:
        await self._write(
            "INSERT INTO runs (run_id, status, state_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, "
            "state_json = excluded.state_json, updated_at = excluded.updated_at",
            (state.run_id, state.status.value, state.model_dump_json(), updated_at.isoformat()),
        )

    async def load_state(self, run_id: str) -> AgentProcessState | None:
        rows = await self._fetch("SELECT state_json FROM runs WHERE run_id = ?", (run_id,))
        return AgentProcessState.model_validate_json(rows[0]["state_json"]) if rows else None

    async def list_runs(self, status: ProcessStatus | None = None) -> list[AgentProcessState]:
        if status is None:
            rows = await self._fetch("SELECT state_json FROM runs ORDER BY updated_at, run_id")
        else:
            rows = await self._fetch(
                "SELECT state_json FROM runs WHERE status = ? ORDER BY updated_at, run_id", (status.value,)
            )
        return [AgentProcessState.model_validate_json(r["state_json"]) for r in rows]

    async def run_totals(self) -> dict[str, tuple[float, float, float]]:
        """Per run: (cost in USD, machine time in ms, human wait in ms), in one query."""
        rows = await self._fetch(
            "SELECT run_id, SUM(cost_usd) AS cost, SUM(compute_ms) AS compute, SUM(wait_ms) AS wait "
            "FROM metrics GROUP BY run_id"
        )
        return {r["run_id"]: (r["cost"] or 0.0, r["compute"] or 0.0, r["wait"] or 0.0) for r in rows}

    async def run_starts(self) -> dict[str, datetime]:
        """When each run started (its first journal entry), in one query."""
        rows = await self._fetch("SELECT run_id, MIN(ts) AS started FROM events GROUP BY run_id")
        return {r["run_id"]: datetime.fromisoformat(r["started"]) for r in rows}

    # --- event journal (append-only) --------------------------------------------------------------------

    async def append_event(
        self,
        run_id: str,
        ts: datetime,
        step: str,
        iteration: int,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._write(
            "INSERT INTO events (run_id, ts, step, iteration, event_type, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, ts.isoformat(), step, iteration, event_type, json.dumps(payload or {})),
        )

    async def list_events(self, run_id: str) -> list[EventRecord]:
        rows = await self._fetch("SELECT * FROM events WHERE run_id = ? ORDER BY id", (run_id,))
        return [
            EventRecord(
                id=r["id"],
                run_id=r["run_id"],
                ts=datetime.fromisoformat(r["ts"]),
                step=r["step"],
                iteration=r["iteration"],
                event_type=r["event_type"],
                payload=json.loads(r["payload_json"]),
            )
            for r in rows
        ]

    # --- approvals (append-only) ------------------------------------------------------------------------

    async def append_approval(self, run_id: str, decision: ApprovalDecision) -> None:
        await self._write(
            "INSERT INTO approvals (run_id, gate, action, actor, comment, decided_at, iteration) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                decision.gate,
                decision.action,
                decision.actor,
                decision.comment,
                decision.decided_at.isoformat(),
                decision.iteration,
            ),
        )

    async def list_approvals(self, run_id: str) -> list[ApprovalDecision]:
        rows = await self._fetch("SELECT * FROM approvals WHERE run_id = ? ORDER BY id", (run_id,))
        return [
            ApprovalDecision(
                gate=r["gate"],
                action=r["action"],
                actor=r["actor"],
                comment=r["comment"],
                decided_at=datetime.fromisoformat(r["decided_at"]),
                iteration=r["iteration"],
            )
            for r in rows
        ]

    # --- LLM cache ----------------------------------------------------------------------------------------

    async def get_llm_cache(
        self, run_id: str, step: str, iteration: int, prompt_hash: str
    ) -> CachedLLMResponse | None:
        rows = await self._fetch(
            "SELECT response_json, usage_json FROM llm_cache "
            "WHERE run_id = ? AND step = ? AND iteration = ? AND prompt_hash = ?",
            (run_id, step, iteration, prompt_hash),
        )
        return CachedLLMResponse(rows[0]["response_json"], rows[0]["usage_json"]) if rows else None

    async def put_llm_cache(
        self, run_id: str, step: str, iteration: int, prompt_hash: str, *, response_json: str, usage_json: str
    ) -> None:
        """First write wins: a replayed step must never overwrite the response it already paid for."""
        await self._write(
            "INSERT OR IGNORE INTO llm_cache "
            "(run_id, step, iteration, prompt_hash, response_json, usage_json) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, step, iteration, prompt_hash, response_json, usage_json),
        )

    async def llm_responses(self, run_id: str) -> dict[tuple[str, int], str]:
        """Every validated LLM reply of a run, by (step, iteration): the run's history, plan by plan."""
        rows = await self._fetch(
            "SELECT step, iteration, response_json FROM llm_cache WHERE run_id = ? ORDER BY rowid", (run_id,)
        )
        return {(r["step"], r["iteration"]): r["response_json"] for r in rows}

    async def llm_cost_usd(self, run_id: str) -> float:
        """Total cost of every paid LLM call of the run (each paid response is cached exactly once)."""
        rows = await self._fetch("SELECT usage_json FROM llm_cache WHERE run_id = ?", (run_id,))
        return sum(float(json.loads(r["usage_json"]).get("cost_usd", 0.0)) for r in rows)

    # --- metrics ------------------------------------------------------------------------------------------

    async def append_metric(self, run_id: str, m: StepMetrics) -> None:
        await self._write(
            "INSERT INTO metrics (run_id, step_name, iteration, model_id, started_at, ended_at, compute_ms, "
            "wait_ms, prompt_tokens, completion_tokens, cost_usd, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                m.step_name,
                m.iteration,
                m.model_id,
                m.started_at.isoformat(),
                m.ended_at.isoformat() if m.ended_at else None,
                m.compute_ms,
                m.wait_ms,
                m.prompt_tokens,
                m.completion_tokens,
                m.cost_usd,
                m.status,
            ),
        )

    async def list_metrics(self, run_id: str | None = None) -> list[StepMetrics]:
        if run_id is None:
            rows = await self._fetch("SELECT * FROM metrics ORDER BY id")
        else:
            rows = await self._fetch("SELECT * FROM metrics WHERE run_id = ? ORDER BY id", (run_id,))
        return [
            StepMetrics(
                step_name=r["step_name"],
                iteration=r["iteration"],
                model_id=r["model_id"],
                started_at=datetime.fromisoformat(r["started_at"]),
                ended_at=datetime.fromisoformat(r["ended_at"]) if r["ended_at"] else None,
                compute_ms=r["compute_ms"],
                wait_ms=r["wait_ms"],
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                cost_usd=r["cost_usd"],
                status=r["status"],
            )
            for r in rows
        ]

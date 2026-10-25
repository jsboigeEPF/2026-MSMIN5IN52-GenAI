import sqlite3
from datetime import timedelta
from pathlib import Path

import aiosqlite
import pytest

from src.models.state import ApprovalDecision, ProcessStatus, StepMetrics
from src.storage.sqlite_store import SqliteStore
from tests.conftest import T0, StateFactory


async def test_save_and_load_state(store: SqliteStore, make_state: StateFactory) -> None:
    state = make_state(execution_plan=["a", "b"])
    await store.save_state(state, T0)
    assert await store.load_state("run-1") == state


async def test_load_missing_state_returns_none(store: SqliteStore) -> None:
    assert await store.load_state("nope") is None


async def test_save_state_upserts(store: SqliteStore, make_state: StateFactory) -> None:
    state = make_state()
    await store.save_state(state, T0)
    state.status = ProcessStatus.PLANNING
    await store.save_state(state, T0 + timedelta(seconds=1))
    runs = await store.list_runs()
    assert [r.status for r in runs] == [ProcessStatus.PLANNING]


async def test_list_runs_filters_by_status(store: SqliteStore, make_state: StateFactory) -> None:
    await store.save_state(make_state(run_id="a", status=ProcessStatus.AWAITING_APPROVAL), T0)
    await store.save_state(make_state(run_id="b", status=ProcessStatus.COMPLETED), T0)
    waiting = await store.list_runs(status=ProcessStatus.AWAITING_APPROVAL)
    assert [r.run_id for r in waiting] == ["a"]


async def test_event_journal_is_ordered_and_keeps_payload(store: SqliteStore) -> None:
    await store.append_event("run-1", T0, "plan", 0, "step_started")
    await store.append_event("run-1", T0, "plan", 0, "step_completed", {"event": "PlanCreated"})
    await store.append_event("other", T0, "plan", 0, "step_started")
    events = await store.list_events("run-1")
    assert [e.event_type for e in events] == ["step_started", "step_completed"]
    assert events[1].payload == {"event": "PlanCreated"}
    assert events[0].ts == T0


@pytest.mark.parametrize("table", ["events", "approvals"])
async def test_journal_tables_are_append_only(store: SqliteStore, db_path: Path, table: str) -> None:
    await store.append_event("run-1", T0, "plan", 0, "step_started")
    await store.append_approval(
        "run-1", ApprovalDecision(gate="plan", action="reject", actor="alice", decided_at=T0, iteration=0)
    )
    async with aiosqlite.connect(db_path) as db:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            await db.execute(f"UPDATE {table} SET run_id = 'x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            await db.execute(f"DELETE FROM {table}")


async def test_approvals_round_trip_in_order(store: SqliteStore) -> None:
    first = ApprovalDecision(
        gate="plan", action="revise", actor="alice", comment="add a test", decided_at=T0, iteration=0
    )
    second = ApprovalDecision(gate="write", action="approve", actor="bob", decided_at=T0, iteration=1)
    await store.append_approval("run-1", first)
    await store.append_approval("run-1", second)
    assert await store.list_approvals("run-1") == [first, second]


async def test_llm_cache_round_trip_and_first_write_wins(store: SqliteStore) -> None:
    key = ("run-1", "code", 0, "hash-1")
    assert await store.get_llm_cache(*key) is None
    await store.put_llm_cache(*key, response_json='{"x": 1}', usage_json='{"cost_usd": 0.1}')
    await store.put_llm_cache(*key, response_json='{"x": 2}', usage_json="{}")
    cached = await store.get_llm_cache(*key)
    assert cached is not None
    assert cached.response_json == '{"x": 1}'
    assert cached.usage_json == '{"cost_usd": 0.1}'
    assert await store.get_llm_cache("run-1", "code", 1, "hash-1") is None


async def test_metrics_round_trip(store: SqliteStore) -> None:
    m = StepMetrics(
        step_name="code",
        iteration=1,
        model_id="vendor/coder",
        started_at=T0,
        ended_at=T0,
        compute_ms=12.5,
        prompt_tokens=100,
        completion_tokens=20,
        cost_usd=0.002,
        status="completed",
    )
    await store.append_metric("run-1", m)
    await store.append_metric("run-2", m)
    assert await store.list_metrics("run-1") == [m]
    assert len(await store.list_metrics()) == 2


async def test_data_survives_reopen(db_path: Path, make_state: StateFactory) -> None:
    async with SqliteStore(db_path) as s:
        await s.save_state(make_state(), T0)
    async with SqliteStore(db_path) as s:
        assert await s.load_state("run-1") is not None


async def test_store_requires_open(db_path: Path) -> None:
    with pytest.raises(RuntimeError):
        await SqliteStore(db_path).list_runs()


async def test_old_databases_get_the_gate_column(db_path: Path) -> None:
    with sqlite3.connect(db_path) as old:
        old.execute(
            "CREATE TABLE approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, "
            "action TEXT NOT NULL, actor TEXT NOT NULL, comment TEXT, decided_at TEXT NOT NULL, "
            "iteration INTEGER NOT NULL)"
        )
        old.execute(
            "INSERT INTO approvals (run_id, action, actor, decided_at, iteration) VALUES (?, ?, ?, ?, ?)",
            ("old-1", "approve", "alice", T0.isoformat(), 0),
        )
    async with SqliteStore(db_path) as store:
        [decision] = await store.list_approvals("old-1")
    assert (decision.gate, decision.action) == ("write", "approve")

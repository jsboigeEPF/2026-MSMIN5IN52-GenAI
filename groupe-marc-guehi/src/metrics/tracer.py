"""Traces JSONL horodatées (schéma : docs/METRICS.md)."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path


class Tracer:
    def __init__(self, run_id: str, trace_dir: str = "data/traces") -> None:
        self.run_id = run_id
        self._t0 = time.monotonic_ns()
        path = Path(trace_dir)
        path.mkdir(parents=True, exist_ok=True)
        self._file = open(path / f"{run_id}.jsonl", "a", encoding="utf-8")  # noqa: SIM115  fermé par close()

    def now_ms(self) -> float:
        return (time.monotonic_ns() - self._t0) / 1e6

    def emit(self, turn: int, event: str, stage: str, provider: str = "", route: str = "cloud",
             data: dict | None = None, cost_eur: float = 0.0) -> None:
        rec = {
            "run_id": self.run_id, "turn": turn, "event": event,
            "t_ms": round(self.now_ms(), 2),
            "ts_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "stage": stage, "provider": provider, "route": route,
            "data": data or {}, "cost_eur": cost_eur,
        }
        self._file.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

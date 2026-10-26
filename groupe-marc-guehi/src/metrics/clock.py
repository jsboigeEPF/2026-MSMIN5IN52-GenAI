"""Horloge unique des durées : `time.monotonic_ns()` (CLAUDE.md). Les instants sont des entiers en nanosecondes."""
from __future__ import annotations

import time


def now_ns() -> int:
    return time.monotonic_ns()


def elapsed_ms(t0_ns: int, now: int | None = None) -> float:
    return ((now_ns() if now is None else now) - t0_ns) / 1e6


def elapsed_s(t0_ns: int, now: int | None = None) -> float:
    return ((now_ns() if now is None else now) - t0_ns) / 1e9

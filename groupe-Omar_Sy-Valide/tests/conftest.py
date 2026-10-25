from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.clock import FakeClock
from src.models.state import AgentProcessState
from src.storage.sqlite_store import SqliteStore

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

StateFactory = Callable[..., AgentProcessState]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def make_state() -> StateFactory:
    def factory(**overrides: Any) -> AgentProcessState:
        fields: dict[str, Any] = {
            "run_id": "run-1",
            "target_repo_path": "/repo",
            "base_branch": "main",
            "target_file": "calc.py",
            "user_prompt": "fix add()",
        }
        fields.update(overrides)
        return AgentProcessState(**fields)

    return factory


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "mini-opencode.db"


@pytest.fixture
async def store(db_path: Path) -> AsyncIterator[SqliteStore]:
    async with SqliteStore(db_path) as s:
        yield s

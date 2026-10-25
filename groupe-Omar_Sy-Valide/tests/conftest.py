import subprocess
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


BUGGY_CALC = 'def add(a: int, b: int) -> int:\n    """Return the sum of a and b."""\n    return a - b\n'
FIXED_CALC = 'def add(a: int, b: int) -> int:\n    """Return the sum of a and b."""\n    return a + b\n'
TEST_CALC = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Target repository: a buggy `calc.py` and its failing test, committed on `main`."""
    repo = tmp_path / "target"
    (repo / "tests").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "calc.py").write_text(BUGGY_CALC, newline="")
    (repo / "tests" / "test_calc.py").write_text(TEST_CALC, newline="")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo

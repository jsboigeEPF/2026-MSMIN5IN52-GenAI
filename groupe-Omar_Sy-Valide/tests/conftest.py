import subprocess
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from semantic_kernel import Kernel

from src.clock import FakeClock
from src.config import Settings
from src.llm.client import LLMClient
from src.llm.fake import FakeLLM
from src.models.state import AgentProcessState
from src.process.engine import ProcessRunner
from src.storage.sqlite_store import SqliteStore
from src.tools.sandbox import SandboxRunner

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

StateFactory = Callable[..., AgentProcessState]
RunnerFactory = Callable[..., ProcessRunner]


@pytest.fixture(autouse=True)
def no_project_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings read the project's `.env` from any cwd; tests must never see the real key or models."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


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


@pytest.fixture
def make_runner(store: SqliteStore, clock: FakeClock) -> RunnerFactory:
    """A fresh ProcessRunner over the shared store: building a second one simulates a process restart.

    Tests run the sandbox in explicit subprocess mode so they do not need Docker.
    """

    def factory(
        fake: FakeLLM,
        fault_hook: Callable[[str], None] | None = None,
        on_store: SqliteStore | None = None,  # a separately opened store simulates a new process
        **cfg: Any,
    ) -> ProcessRunner:
        settings = Settings(_env_file=None, **{"SANDBOX_MODE": "subprocess", **cfg})
        db = on_store or store
        return ProcessRunner(
            store=db,
            llm=LLMClient(Kernel(), fake, db, settings),
            sandbox=SandboxRunner("subprocess", settings.SANDBOX_TIMEOUT_SEC, settings.SANDBOX_IMAGE),
            settings=settings,
            clock=clock,
            fault_hook=fault_hook,
        )

    return factory

import asyncio
from pathlib import Path

import pytest

from src.tools import sandbox
from src.tools.git_ops import list_files
from src.tools.inspector import CodeInspector
from src.tools.patch import PatchEngine
from src.tools.sandbox import SandboxRunner, SandboxUnavailableError
from src.tools.workspace import WorkspaceManager
from tests.conftest import BUGGY_CALC, FIXED_CALC, git

# --- WorkspaceManager ---------------------------------------------------------------------------------


def test_create_worktree_on_run_branch_without_touching_base(git_repo: Path) -> None:
    base_sha = git(git_repo, "rev-parse", "main")
    path, branch = WorkspaceManager(git_repo, "main").create("run-1")

    assert path == git_repo / ".mini-opencode" / "runs" / "run-1"
    assert branch == "mini-opencode/run-1"
    assert (path / "calc.py").read_text() == BUGGY_CALC
    assert git(path, "branch", "--show-current") == branch
    assert git(git_repo, "rev-parse", "main") == base_sha
    assert git(git_repo, "status", "--porcelain") == ""  # .mini-opencode/ is excluded locally


def test_create_mirrors_uncommitted_and_untracked_changes(git_repo: Path) -> None:
    (git_repo / "calc.py").write_text(FIXED_CALC)
    (git_repo / "notes.py").write_text("x = 1\n")
    (git_repo / "tests" / "test_calc.py").unlink()
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    assert (path / "calc.py").read_text() == FIXED_CALC
    assert (path / "notes.py").read_text() == "x = 1\n"
    assert not (path / "tests" / "test_calc.py").exists()


def test_create_is_idempotent(git_repo: Path) -> None:
    ws = WorkspaceManager(git_repo, "main")
    first = ws.create("run-1")
    (first[0] / "calc.py").write_text(FIXED_CALC)
    assert ws.create("run-1") == first
    assert (first[0] / "calc.py").read_text() == FIXED_CALC


def test_create_reuses_branch_left_by_a_crash(git_repo: Path) -> None:
    ws = WorkspaceManager(git_repo, "main")
    path, branch = ws.create("run-1")
    git(git_repo, "worktree", "remove", "--force", str(path))
    assert ws.create("run-1") == (path, branch)
    assert (path / "calc.py").exists()


def test_discard_removes_worktree_and_branch_idempotently(git_repo: Path) -> None:
    ws = WorkspaceManager(git_repo, "main")
    path, branch = ws.create("run-1")
    ws.discard("run-1")
    ws.discard("run-1")
    ws.discard("never-created")
    assert not path.exists()
    assert git(git_repo, "branch", "--list", branch) == ""


@pytest.mark.parametrize("run_id", ["", "../x", "a b", "a/b"])
def test_unsafe_run_ids_are_rejected(git_repo: Path, run_id: str) -> None:
    with pytest.raises(ValueError):
        WorkspaceManager(git_repo, "main").create(run_id)


# --- CodeInspector ------------------------------------------------------------------------------------

MODULE = '''"""Shapes."""
import math


class Circle(Base):
    """A circle."""

    def __init__(self, r: float) -> None:
        self.r = r

    def area(self) -> float:
        """Area of the circle.

        Long description.
        """
        return math.pi * self.r**2


async def load(path: str, *, strict: bool = False) -> "Circle":
    return Circle(1.0)
'''


def test_read_file(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n")
    assert CodeInspector().read_file(tmp_path / "m.py") == "x = 1\n"


def test_inspect_ast_outlines_signatures_and_docstrings(tmp_path: Path) -> None:
    (tmp_path / "shapes.py").write_text(MODULE)
    outline = CodeInspector().inspect_ast(tmp_path / "shapes.py")
    assert outline.splitlines() == [
        '"""Shapes."""',
        "class Circle(Base):",
        '    """A circle."""',
        "    def __init__(self, r: float) -> None",
        "    def area(self) -> float",
        '        """Area of the circle."""',
        "async def load(path: str, *, strict: bool=False) -> 'Circle'",
    ]
    assert "math.pi" not in outline


def test_inspect_ast_reports_syntax_errors(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("def broken(:\n")
    assert CodeInspector().inspect_ast(tmp_path / "bad.py").startswith("SyntaxError")


# --- PatchEngine --------------------------------------------------------------------------------------


def test_diff_is_pure_and_shows_the_change() -> None:
    diff = PatchEngine.diff("calc.py", BUGGY_CALC, FIXED_CALC)
    assert "--- a/calc.py" in diff and "+++ b/calc.py" in diff
    assert "-    return a - b" in diff and "+    return a + b" in diff
    assert PatchEngine.diff("calc.py", BUGGY_CALC, FIXED_CALC) == diff


def test_diff_of_a_new_file() -> None:
    assert "+x = 1" in PatchEngine.diff("pkg/new.py", "", "x = 1\n")


def test_apply_writes_only_under_its_root(git_repo: Path) -> None:
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    assert PatchEngine().apply(path, "calc.py", FIXED_CALC) == (path / "calc.py").resolve()
    assert (path / "calc.py").read_text() == FIXED_CALC
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC  # user's tree untouched


def test_apply_can_create_a_new_file(tmp_path: Path) -> None:
    PatchEngine().apply(tmp_path, "pkg/new.py", "x = 1\n")
    assert (tmp_path / "pkg" / "new.py").read_text() == "x = 1\n"


@pytest.mark.parametrize(
    "file",
    ["../escape.py", "/abs.py", "C:/abs.py", "", "  ", ".", ".git/config", "a/.mini-opencode/x.py", "pkg"],
)
def test_apply_refuses_unsafe_paths(tmp_path: Path, file: str) -> None:
    (tmp_path / "pkg").mkdir()
    with pytest.raises(ValueError):
        PatchEngine().apply(tmp_path, file, "x = 1\n")


# --- SandboxRunner ------------------------------------------------------------------------------------


def subprocess_runner(timeout: int = 60) -> SandboxRunner:
    return SandboxRunner(mode="subprocess", timeout_sec=timeout, image="unused")


async def test_pytest_fails_on_buggy_code_and_passes_on_fixed(git_repo: Path) -> None:
    runner = subprocess_runner()
    passed, output = await runner.run_pytest(git_repo)
    assert not passed and "1 failed" in output
    (git_repo / "calc.py").write_text(FIXED_CALC)
    passed, output = await runner.run_pytest(git_repo)
    assert passed and "1 passed" in output


async def test_linter_and_pytest_run_concurrently(git_repo: Path) -> None:
    (git_repo / "calc.py").write_text("import os\n\n" + FIXED_CALC)
    (pytest_ok, _), (lint_ok, lint_out) = await asyncio.gather(
        subprocess_runner().run_pytest(git_repo), subprocess_runner().run_linter(git_repo, "calc.py")
    )
    assert pytest_ok
    assert not lint_ok and "F401" in lint_out
    (git_repo / "calc.py").write_text(FIXED_CALC)
    assert (await subprocess_runner().run_linter(git_repo, "calc.py"))[0]


async def test_timeout_counts_as_failure(git_repo: Path) -> None:
    (git_repo / "tests" / "test_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(30)\n"
    )
    passed, output = await subprocess_runner(timeout=2).run_pytest(git_repo)
    assert not passed and "timed out after 2s" in output


async def test_subprocess_environment_has_no_secrets(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-must-not-leak")
    (git_repo / "tests" / "test_env.py").write_text(
        "import os\n\n\ndef test_env():\n    assert 'OPENROUTER_API_KEY' not in os.environ\n"
    )
    (git_repo / "calc.py").write_text(FIXED_CALC)
    passed, output = await subprocess_runner().run_pytest(git_repo)
    assert passed, output


def test_docker_mode_fails_closed_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "docker_available", lambda image: False)
    with pytest.raises(SandboxUnavailableError, match="SANDBOX_MODE=docker"):
        SandboxRunner(mode="docker", timeout_sec=10, image="img")


def test_docker_command_isolates_network_and_mounts_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "docker_available", lambda image: True)
    cmd = SandboxRunner(mode="docker", timeout_sec=10, image="img")._docker_cmd(tmp_path, ["pytest"], "c1")
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == cmd[cmd.index("--network") : cmd.index("--network") + 2]
    assert f"{tmp_path}:/work:ro" in cmd and cmd[-2:] == ["img", "pytest"]


@pytest.mark.skipif(
    not sandbox.docker_available("mini-opencode-sandbox:latest"),
    reason="Docker daemon or sandbox image unavailable (docker build -t mini-opencode-sandbox sandbox/)",
)
async def test_docker_sandbox_runs_pytest(git_repo: Path) -> None:
    runner = SandboxRunner(mode="docker", timeout_sec=120, image="mini-opencode-sandbox:latest")
    passed, output = await runner.run_pytest(git_repo)
    assert not passed and "1 failed" in output


@pytest.mark.skipif(
    not sandbox.docker_available("mini-opencode-sandbox:latest"),
    reason="Docker daemon or sandbox image unavailable (docker build -t mini-opencode-sandbox sandbox/)",
)
async def test_docker_sandbox_has_no_network(git_repo: Path) -> None:
    (git_repo / "calc.py").write_text(FIXED_CALC)
    (git_repo / "tests" / "test_net.py").write_text(
        "import socket\n\nimport pytest\n\n\ndef test_no_network():\n"
        "    with pytest.raises(OSError):\n"
        "        socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
    )
    runner = SandboxRunner(mode="docker", timeout_sec=120, image="mini-opencode-sandbox:latest")
    passed, output = await runner.run_pytest(git_repo)
    assert passed, output


@pytest.mark.skipif(
    not sandbox.docker_available("mini-opencode-sandbox:latest"),
    reason="Docker daemon or sandbox image unavailable (docker build -t mini-opencode-sandbox sandbox/)",
)
async def test_docker_sandbox_cannot_modify_the_worktree(git_repo: Path) -> None:
    (git_repo / "calc.py").write_text(FIXED_CALC)
    (git_repo / "tests" / "test_tamper.py").write_text(
        "import pytest\n\n\ndef test_tamper():\n"
        "    with pytest.raises(OSError):\n"
        "        open('calc.py', 'w').write('tampered')\n"
    )
    runner = SandboxRunner(mode="docker", timeout_sec=120, image="mini-opencode-sandbox:latest")
    passed, output = await runner.run_pytest(git_repo)
    assert passed, output
    assert (git_repo / "calc.py").read_text() == FIXED_CALC


@pytest.mark.skipif(
    not sandbox.docker_available("mini-opencode-sandbox:latest"),
    reason="Docker daemon or sandbox image unavailable (docker build -t mini-opencode-sandbox sandbox/)",
)
async def test_docker_linter_ignores_permission_bits_of_the_mount(git_repo: Path) -> None:
    # Docker Desktop bind mounts make every file look executable (0777); ruff's EXE rules would
    # then flag clean code, and the coder would "fix" it by adding a shebang (seen in a live run).
    (git_repo / "calc.py").write_text(FIXED_CALC)
    runner = SandboxRunner(mode="docker", timeout_sec=120, image="mini-opencode-sandbox:latest")
    passed, output = await runner.run_linter(git_repo, "calc.py")
    assert passed, output


# --- git helpers ----------------------------------------------------------------------------------------


def test_list_files_includes_untracked_but_not_ignored_or_run_files(git_repo: Path) -> None:
    (git_repo / ".gitignore").write_text("*.log\n")
    (git_repo / "debug.log").write_text("x")
    (git_repo / "new_module.py").write_text("x = 1\n")
    WorkspaceManager(git_repo, "main").create("run-1")
    assert list_files(git_repo) == [".gitignore", "calc.py", "new_module.py", "tests/test_calc.py"]


def test_mirroring_ignores_the_users_diff_config(git_repo: Path) -> None:
    git(git_repo, "config", "color.ui", "always")
    git(git_repo, "config", "diff.noprefix", "true")
    (git_repo / "calc.py").write_text(FIXED_CALC)
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    assert (path / "calc.py").read_text() == FIXED_CALC


def test_an_interrupted_mirroring_is_redone(git_repo: Path) -> None:
    ws = WorkspaceManager(git_repo, "main")
    path, _ = ws.create("run-1")
    ws._mirrored_marker(path).unlink()  # pyright: ignore[reportPrivateUsage]  (simulates the crash)
    (path / "calc.py").write_text("half-copied")
    (git_repo / "calc.py").write_text(FIXED_CALC)
    assert ws.create("run-1")[0] == path
    assert (path / "calc.py").read_text() == FIXED_CALC


async def test_a_repository_without_tests_passes(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n")
    passed, output = await subprocess_runner().run_pytest(tmp_path, allow_no_tests=True)
    assert passed and "no tests in the repository" in output
    assert not (await subprocess_runner().run_pytest(tmp_path))[
        0
    ]  # a repository that had tests and lost them


async def test_linter_only_counts_violations_the_change_introduces(git_repo: Path) -> None:
    # The repository's own ruff config (RUF100 = unused noqa) flags a line the agent did not write.
    (git_repo / "pyproject.toml").write_text('[tool.ruff.lint]\nextend-select = ["RUF100"]\n')
    original = "import os  # noqa: E402\n\n\nprint(os.sep)\n"
    (git_repo / "app.py").write_text(original + "x = 1\n")
    passed, output = await subprocess_runner().run_linter(git_repo, "app.py", baseline=original)
    assert passed, output
    assert "pre-existing violation(s) ignored" in output

    (git_repo / "app.py").write_text(original + "import sys\n")
    passed, output = await subprocess_runner().run_linter(git_repo, "app.py", baseline=original)
    assert not passed and "app.py:5:8: F401" in output and "RUF100" not in output

    # Same rule as a pre-existing violation, different symbol: still new.
    legacy = "print(undefined_a)\n"
    (git_repo / "app.py").write_text("print(undefined_b)\n")
    passed, output = await subprocess_runner().run_linter(git_repo, "app.py", baseline=legacy)
    assert not passed and "undefined_b" in output

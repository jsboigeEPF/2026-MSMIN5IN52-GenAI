import asyncio
from pathlib import Path

import pytest

from src.tools import sandbox
from src.tools.git_ops import GitError, GitOps
from src.tools.inspector import CodeInspector
from src.tools.patch import PatchEngine
from src.tools.sandbox import SandboxRunner
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


def test_apply_writes_file_and_returns_diff_against_base(git_repo: Path) -> None:
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    diff = PatchEngine().apply(path, "calc.py", FIXED_CALC)
    assert (path / "calc.py").read_text() == FIXED_CALC
    assert "--- a/calc.py" in diff and "+++ b/calc.py" in diff
    assert "-    return a - b" in diff and "+    return a + b" in diff
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC  # user's tree untouched


def test_reapplying_the_same_patch_gives_the_same_diff(git_repo: Path) -> None:
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    engine = PatchEngine()
    assert engine.apply(path, "calc.py", FIXED_CALC) == engine.apply(path, "calc.py", FIXED_CALC)


def test_apply_can_create_a_new_file(git_repo: Path) -> None:
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    diff = PatchEngine().apply(path, "pkg/new.py", "x = 1\n")
    assert (path / "pkg" / "new.py").read_text() == "x = 1\n"
    assert "+x = 1" in diff


@pytest.mark.parametrize("file", ["../escape.py", "/abs.py", "C:/abs.py"])
def test_apply_refuses_paths_outside_the_worktree(git_repo: Path, file: str) -> None:
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    with pytest.raises(ValueError):
        PatchEngine().apply(path, file, "x = 1\n")


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


def test_auto_mode_falls_back_to_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "docker_available", lambda image: False)
    assert SandboxRunner(mode="auto", timeout_sec=10, image="img").mode == "subprocess"
    monkeypatch.setattr(sandbox, "docker_available", lambda image: True)
    assert SandboxRunner(mode="auto", timeout_sec=10, image="img").mode == "docker"


def test_docker_command_isolates_network_and_mounts_worktree(tmp_path: Path) -> None:
    cmd = SandboxRunner(mode="docker", timeout_sec=10, image="img")._docker_cmd(tmp_path, ["pytest"], "c1")
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == cmd[cmd.index("--network") : cmd.index("--network") + 2]
    assert f"{tmp_path}:/work" in cmd and cmd[-2:] == ["img", "pytest"]


@pytest.mark.skipif(
    not sandbox.docker_available("mini-opencode-sandbox:latest"),
    reason="Docker daemon or sandbox image unavailable (docker build -t mini-opencode-sandbox sandbox/)",
)
async def test_docker_sandbox_runs_pytest(git_repo: Path) -> None:
    runner = SandboxRunner(mode="docker", timeout_sec=120, image="mini-opencode-sandbox:latest")
    passed, output = await runner.run_pytest(git_repo)
    assert not passed and "1 failed" in output


# --- GitOps -------------------------------------------------------------------------------------------


def patched_worktree(git_repo: Path) -> Path:
    path, _ = WorkspaceManager(git_repo, "main").create("run-1")
    PatchEngine().apply(path, "calc.py", FIXED_CALC)
    return path


def test_commit_on_run_branch_is_idempotent(git_repo: Path) -> None:
    base_sha = git(git_repo, "rev-parse", "main")
    path = patched_worktree(git_repo)
    ops = GitOps()
    sha = ops.commit(path, "calc.py", "fix: add")
    assert git(git_repo, "rev-parse", "mini-opencode/run-1") == sha
    assert ops.commit(path, "calc.py", "fix: add") == sha
    assert git(git_repo, "rev-parse", "main") == base_sha


def test_merge_ff_moves_base_branch(git_repo: Path) -> None:
    ops = GitOps()
    sha = ops.commit(patched_worktree(git_repo), "calc.py", "fix: add")
    assert ops.merge_ff(git_repo, "main", "mini-opencode/run-1") == sha
    assert (git_repo / "calc.py").read_text() == FIXED_CALC


def test_merge_ff_refuses_diverged_base(git_repo: Path) -> None:
    ops = GitOps()
    ops.commit(patched_worktree(git_repo), "calc.py", "fix: add")
    (git_repo / "README.md").write_text("diverge\n")
    git(git_repo, "add", "README.md")
    git(git_repo, "commit", "-q", "-m", "diverge")
    with pytest.raises(GitError):
        ops.merge_ff(git_repo, "main", "mini-opencode/run-1")


def test_merge_ff_requires_base_checked_out(git_repo: Path) -> None:
    ops = GitOps()
    ops.commit(patched_worktree(git_repo), "calc.py", "fix: add")
    git(git_repo, "switch", "-q", "-c", "elsewhere")
    with pytest.raises(GitError, match="checked out"):
        ops.merge_ff(git_repo, "main", "mini-opencode/run-1")


def test_revert_restores_content_and_is_idempotent(git_repo: Path) -> None:
    ops = GitOps()
    sha = ops.commit(patched_worktree(git_repo), "calc.py", "fix: add")
    ops.merge_ff(git_repo, "main", "mini-opencode/run-1")
    revert_sha = ops.revert(git_repo, sha)
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC
    assert ops.revert(git_repo, sha) == revert_sha
    assert git(git_repo, "rev-parse", "main") == revert_sha

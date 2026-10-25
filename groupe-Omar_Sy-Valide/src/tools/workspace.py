"""One git worktree per run (SPEC §4.1). The user's working tree and base branch are never modified here."""

import re
import shutil
from pathlib import Path

from src.tools.git_ops import run_git

RUNS_DIR = Path(".mini-opencode") / "runs"
_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class WorkspaceManager:
    def __init__(self, repo_path: Path, base_branch: str) -> None:
        self.repo = Path(repo_path).resolve()
        self.base_branch = base_branch

    def worktree_path(self, run_id: str) -> Path:
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError(f"unsafe run_id: {run_id!r}")
        return self.repo / RUNS_DIR / run_id

    @staticmethod
    def run_branch(run_id: str) -> str:
        return f"mini-opencode/{run_id}"

    def create(self, run_id: str) -> tuple[Path, str]:
        path, branch = self.worktree_path(run_id), self.run_branch(run_id)
        self._exclude_runs_dir()
        if self._is_registered(path):
            return path, branch
        run_git(self.repo, "worktree", "prune")
        if self._branch_exists(branch):  # left behind by a crash between branch and worktree creation
            run_git(self.repo, "worktree", "add", "-q", str(path), branch)
        else:
            run_git(self.repo, "worktree", "add", "-q", "-b", branch, str(path), self.base_branch)
        return path, branch

    def discard(self, run_id: str) -> None:
        path, branch = self.worktree_path(run_id), self.run_branch(run_id)
        if self._is_registered(path):
            run_git(self.repo, "worktree", "remove", "--force", str(path))
        shutil.rmtree(path, ignore_errors=True)
        run_git(self.repo, "worktree", "prune")
        if self._branch_exists(branch):
            run_git(self.repo, "branch", "-q", "-D", branch)

    def _is_registered(self, path: Path) -> bool:
        listing = run_git(self.repo, "worktree", "list", "--porcelain")
        registered = [
            Path(line[len("worktree ") :]) for line in listing.splitlines() if line.startswith("worktree ")
        ]
        return path.exists() and any(p.resolve() == path.resolve() for p in registered)

    def _branch_exists(self, branch: str) -> bool:
        return run_git(self.repo, "branch", "--list", branch) != ""

    def _exclude_runs_dir(self) -> None:
        """Keep run worktrees out of the user's `git status` via the local, uncommitted exclude file."""
        common = Path(run_git(self.repo, "rev-parse", "--git-common-dir"))
        exclude = (common if common.is_absolute() else self.repo / common) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ".mini-opencode/" not in existing.splitlines():
            exclude.write_text(existing.rstrip("\n") + "\n.mini-opencode/\n", encoding="utf-8")

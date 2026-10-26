"""One git worktree per run (SPEC §4.1). The user's working tree and branch are never modified here.

The worktree starts from the user's branch and receives their uncommitted changes (tracked edits and
untracked files), so the tests run on the same tree the approved file will be written into.
"""

import re
import shutil
import subprocess
from pathlib import Path

from src.tools.git_ops import GitError, run_git

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
            if self._mirrored_marker(path).exists():
                return path, branch
            self.discard(run_id)  # a crash interrupted the mirroring: start this worktree over
        run_git(self.repo, "worktree", "prune")
        if self._branch_exists(branch):  # left behind by a crash between branch and worktree creation
            run_git(self.repo, "worktree", "add", "-q", str(path), branch)
        else:
            run_git(self.repo, "worktree", "add", "-q", "-b", branch, str(path), self.base_branch)
        self._mirror_uncommitted_changes(path)
        self._mirrored_marker(path).touch()
        return path, branch

    @staticmethod
    def _mirrored_marker(path: Path) -> Path:
        """Lives in the worktree's private git dir: invisible to the tests and to `git status`."""
        return Path(run_git(path, "rev-parse", "--absolute-git-dir")) / "omar-mirrored"

    def _mirror_uncommitted_changes(self, path: Path) -> None:
        # Pinned format: the user's git config (colors, external diff, prefixes) must not change the patch.
        diff = subprocess.run(
            [
                "git",
                "diff",
                "HEAD",
                "--binary",
                "--no-color",
                "--no-ext-diff",
                "--src-prefix=a/",
                "--dst-prefix=b/",
            ],
            cwd=self.repo,
            capture_output=True,
            check=False,
        )
        if diff.returncode != 0:
            raise GitError(f"git diff HEAD failed: {diff.stderr.decode(errors='replace').strip()}")
        if diff.stdout:
            applied = subprocess.run(
                ["git", "apply", "--whitespace=nowarn"], cwd=path, input=diff.stdout, capture_output=True
            )
            if applied.returncode != 0:
                raise GitError(f"git apply failed: {applied.stderr.decode(errors='replace').strip()}")
        untracked = run_git(self.repo, "ls-files", "--others", "--exclude-standard")
        for name in untracked.splitlines():
            if name and not name.startswith(".mini-opencode/"):
                (path / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.repo / name, path / name)

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

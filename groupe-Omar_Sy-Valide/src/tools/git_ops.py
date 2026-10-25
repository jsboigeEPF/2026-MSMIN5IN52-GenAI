"""Git operations used by finalization and compensation (SPEC §4.5). Deterministic, idempotent."""

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run_git(cwd: Path, *args: str, strip: bool = True) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip() if strip else done.stdout


class GitOps:
    def commit(self, worktree_path: Path, file: str, message: str) -> str:
        """Commit `file` on the worktree's run branch. With nothing to commit, returns the current HEAD."""
        run_git(worktree_path, "add", "--", file)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=worktree_path)
        if staged.returncode != 0:
            run_git(worktree_path, "commit", "-q", "-m", message)
        return run_git(worktree_path, "rev-parse", "HEAD")

    def merge_ff(self, repo: Path, base_branch: str, run_branch: str) -> str:
        """Fast-forward `base_branch` to `run_branch` in the repository's main working tree."""
        current = run_git(repo, "branch", "--show-current")
        if current != base_branch:
            raise GitError(f"base branch '{base_branch}' must be checked out in {repo} (found '{current}')")
        run_git(repo, "merge", "--ff-only", "-q", run_branch)
        return run_git(repo, "rev-parse", "HEAD")

    def revert(self, repo: Path, sha: str) -> str:
        """Revert `sha` on the checked-out branch. If already reverted, return the existing revert commit."""
        existing = run_git(repo, "log", "--format=%H", f"--grep=This reverts commit {sha}", "-n", "1")
        if existing:
            return existing
        run_git(repo, "revert", "--no-edit", sha)
        return run_git(repo, "rev-parse", "HEAD")

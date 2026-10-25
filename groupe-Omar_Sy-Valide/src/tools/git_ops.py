"""Git helpers used by the workspace and the planner (SPEC §4.5). Deterministic, read-only on user files."""

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


def list_files(repo: Path) -> list[str]:
    """Tracked and untracked (not ignored) files of the working tree, as repository-relative POSIX paths."""
    listing = run_git(repo, "ls-files", "--cached", "--others", "--exclude-standard")
    return sorted({line for line in listing.splitlines() if line and not line.startswith(".mini-opencode/")})

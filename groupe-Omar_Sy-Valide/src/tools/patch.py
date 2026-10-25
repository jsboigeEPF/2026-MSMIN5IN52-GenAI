"""Write generated code into the run worktree only (SPEC §4.3)."""

import difflib
from pathlib import Path, PurePosixPath, PureWindowsPath

from src.tools.git_ops import GitError, run_git


class PatchEngine:
    def apply(self, worktree_path: Path, file: str, new_content: str) -> str:
        """Overwrite `file` and return the unified diff against the run's base commit.

        Diffing against the base commit (not the file on disk) keeps the diff identical when a step is
        replayed after a crash, and shows the reviewer the cumulative change across fix iterations.
        """
        target = self._resolve(worktree_path, file)
        try:
            base = run_git(worktree_path, "show", f"HEAD:{PurePosixPath(file)}", strip=False)
        except GitError:
            base = ""  # new file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, encoding="utf-8", newline="")
        return "".join(
            difflib.unified_diff(
                base.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{file}",
                tofile=f"b/{file}",
            )
        )

    @staticmethod
    def _resolve(worktree_path: Path, file: str) -> Path:
        if (
            PurePosixPath(file).is_absolute()
            or PureWindowsPath(file).is_absolute()
            or PureWindowsPath(file).drive
        ):
            raise ValueError(f"absolute path not allowed: {file}")
        root = Path(worktree_path).resolve()
        target = (root / file).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"path escapes the worktree: {file}")
        return target

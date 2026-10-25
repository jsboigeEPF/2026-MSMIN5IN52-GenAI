"""Diff and write generated code (SPEC §4.3). Computing the diff never touches the disk."""

import difflib
from pathlib import Path, PurePosixPath, PureWindowsPath

_PROTECTED_DIRS = {".git", ".mini-opencode"}  # never written by the agent


class PatchEngine:
    @staticmethod
    def diff(file: str, old: str, new: str) -> str:
        """Unified diff shown to the reviewer before anything is written.

        Diffing against the content read at the start of the run (not the file on disk) keeps the diff
        identical when a step is replayed after a crash, and shows the cumulative change across fixes.
        """
        return "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{file}",
                tofile=f"b/{file}",
            )
        )

    def apply(self, root: Path, file: str, new_content: str) -> Path:
        """Overwrite `file` under `root` (a run worktree or, once approved, the user's working tree)."""
        target = self.resolve(root, file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, encoding="utf-8", newline="")
        return target

    @staticmethod
    def resolve(root: Path, file: str) -> Path:
        """`root / file`, refusing absolute paths and paths that escape `root`."""
        if (
            not file.strip()
            or PurePosixPath(file).is_absolute()
            or PureWindowsPath(file).is_absolute()
            or PureWindowsPath(file).drive
        ):
            raise ValueError(f"invalid or absolute path not allowed: {file!r}")
        base = Path(root).resolve()
        target = (base / file).resolve()
        if not target.is_relative_to(base) or target == base:
            raise ValueError(f"path escapes the repository: {file}")
        if _PROTECTED_DIRS & set(target.relative_to(base).parts):
            raise ValueError(f"path inside git or run metadata not allowed: {file}")
        if target.is_dir():
            raise ValueError(f"{file} is a directory, not a file")
        return target

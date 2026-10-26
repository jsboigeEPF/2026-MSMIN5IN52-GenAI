"""`@file` mentions in the task prompt: detect the one being typed, rank the repository's files, insert one.

Pure functions over strings, so the completion popup is tested without a terminal.
"""

import re

_MENTION = re.compile(r"(?:^|(?<=\s))@([^\s@]*)$")
_MENTIONED = re.compile(r"(?:^|(?<=\s))@([^\s@]+)")
MAX_SUGGESTIONS = 8


def mention_at(value: str, cursor: int) -> tuple[int, str] | None:
    """The mention being typed just before the cursor: (index of its "@", query), or None."""
    found = _MENTION.search(value[:cursor])
    return (found.start(), found.group(1)) if found else None


def _rank(path: str, query: str) -> tuple[int, int, str] | None:
    """Lower is better: file name starts with the query, then contains it, then the path does, then the
    letters appear in order. None when the path does not match at all."""
    q, lowered = query.lower(), path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if not q:
        return (3, lowered.count("/"), lowered)  # nothing typed yet: shallow files first
    if name.startswith(q):
        return (0, len(name), lowered)
    if q in name:
        return (1, len(name), lowered)
    if q in lowered:
        return (2, len(lowered), lowered)
    position = 0
    for char in q:
        position = lowered.find(char, position) + 1
        if position == 0:
            return None
    return (4, len(lowered), lowered)


def match_files(files: list[str], query: str, limit: int = MAX_SUGGESTIONS) -> list[str]:
    ranked = [(rank, path) for path in files if (rank := _rank(path, query)) is not None]
    return [path for _, path in sorted(ranked)[:limit]]


def insert_mention(value: str, cursor: int, start: int, path: str) -> tuple[str, int]:
    """Replace the mention being typed (from its "@" to the cursor) with `@path `; returns (value, cursor)."""
    inserted = f"@{path} "
    rest = value[cursor:].lstrip(" ")
    return value[:start] + inserted + rest, start + len(inserted)


def mentioned_files(text: str, files: list[str]) -> list[str]:
    """The repository files mentioned with `@` in a prompt, in order, without duplicates."""
    known = set(files)
    seen: list[str] = []
    for path in _MENTIONED.findall(text):
        if path in known and path not in seen:
            seen.append(path)
    return seen

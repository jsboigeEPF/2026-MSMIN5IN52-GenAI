"""Incremental sentence segmentation with a bound on unpunctuated output."""

import re

ABBREVIATIONS = {"m.", "mme.", "dr.", "pr.", "etc.", "ex.", "p.ex."}


class SentenceBuffer:
    def __init__(self, max_chars: int = 180):
        self.buffer = ""
        self.max_chars = max_chars

    def feed(self, token: str) -> list[str]:
        self.buffer += token
        result = []
        while True:
            end = self._boundary()
            if end is None:
                break
            phrase = self.buffer[:end].strip()
            self.buffer = self.buffer[end:].lstrip()
            if phrase:
                result.append(phrase)
        return result

    def _boundary(self) -> int | None:
        # A following character avoids cutting a decimal at a token boundary.
        for match in re.finditer(r"[.!?…;:,](?=\s)", self.buffer):
            end = match.end()
            if end > self.max_chars:
                break
            prefix = self.buffer[:end]
            last_word = prefix.split()[-1].lower()
            if last_word in ABBREVIATIONS:
                continue
            if match.group() in ",:" and len(prefix.split()) < 8:
                continue
            return end
        if len(self.buffer) >= self.max_chars:
            split = self.buffer.rfind(" ", 0, self.max_chars)
            return split if split > 0 else self.max_chars
        return None

    def flush(self) -> list[str]:
        tail = self.buffer.strip()
        self.buffer = ""
        return [tail] if tail else []

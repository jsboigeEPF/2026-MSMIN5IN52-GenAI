"""Découpe du flux de tokens du LLM en morceaux à envoyer au TTS (le 1er est volontairement court)."""
from __future__ import annotations

import re

FIRST_PUNCT = re.compile(r"[,;:.!?…]\s")
REST_PUNCT = re.compile(r"[.!?…]+\s|\n")


class SentenceChunker:
    def __init__(self, first_min: int = 8, rest_min: int = 20) -> None:
        self.first_min, self.rest_min = first_min, rest_min
        self._buf = ""
        self._emitted = 0

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out = []
        while (chunk := self._next()) is not None:
            out.append(chunk)
        return out

    def flush(self) -> list[str]:
        out = self.feed("")
        if self._buf.strip():
            out.append(self._buf.strip())
        self._buf = ""
        return out

    def _next(self) -> str | None:
        pat, lim = (FIRST_PUNCT, self.first_min) if self._emitted == 0 else (REST_PUNCT, self.rest_min)
        for m in pat.finditer(self._buf):
            if m.end() >= lim:
                chunk, self._buf = self._buf[:m.end()].strip(), self._buf[m.end():]
                self._emitted += 1
                return chunk
        return None

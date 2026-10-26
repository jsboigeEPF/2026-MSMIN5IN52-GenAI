"""Faux fournisseurs pour --dry-run et les tests : aucun appel réseau, aucun coût."""
from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Callable

import numpy as np

from src.audio.pcm import SAMPLE_RATE_OUT
from src.interfaces import SearchNotice

SEARCH_WORDS = ("météo", "actualité")
CANNED = "Bien sûr. Je peux t'aider avec ça. Commence par une question simple, puis explique le principe."


class FakeSTT:
    name = "fake-stt"

    def __init__(self, text: str = "Bonjour, ceci est une transcription factice.", on_event: Callable | None = None):
        self.text, self.emit = text, on_event or (lambda *a, **k: None)

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        self.emit("stt_request", provider=self.name)
        await asyncio.sleep(0.01)
        self.emit("stt_final", provider=self.name, text=self.text)
        return self.text

    async def warmup(self) -> None:
        pass


class BrokenSTT:
    """STT toujours en panne (injection de panne : VOICE_FAIL_STT=1, tests de bascule)."""
    name = "broken-stt"

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        raise RuntimeError("panne simulée du STT")

    async def warmup(self) -> None:
        pass


class FakeLLM:
    def __init__(self, reply: str = CANNED, on_event: Callable | None = None, announce: str = ""):
        self.reply, self.emit, self.announce = reply, on_event or (lambda *a, **k: None), announce

    async def stream(self, messages: list[dict], max_tokens: int | None = None,
                     allow_search: bool = True) -> AsyncIterator[str | SearchNotice]:
        self.emit("llm_request", provider="fake-llm")
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "").lower()
        if allow_search and any(w in last for w in SEARCH_WORDS):  # simule un tour avec recherche internet
            self.emit("llm_tool_call", provider="fake-llm", tool="web_search")
            yield SearchNotice(last, self.announce)
            await asyncio.sleep(0.3)
            self.emit("search_done", provider="fake-search", ms=300.0)
        for i, word in enumerate(self.reply.split(" ")):
            await asyncio.sleep(0.005)
            if i == 0:
                self.emit("llm_first_token", provider="fake-llm")
            yield word + " "
        self.emit("llm_done", provider="fake-llm")

    async def warmup(self) -> None:
        pass


class FakeTTS:
    """Un ton de 220 Hz dont la durée est proportionnelle au texte (60 ms par caractère)."""

    def __init__(self, voice: str = "fake", on_event: Callable | None = None):
        self.voice, self.emit = voice, on_event or (lambda *a, **k: None)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        self.emit("tts_request", provider="fake-tts", chars=len(text))
        n = int(SAMPLE_RATE_OUT * 0.06 * len(text))
        t = np.arange(n) / SAMPLE_RATE_OUT
        pcm = (3000 * np.sin(2 * math.pi * 220 * t)).astype(np.int16).tobytes()
        step = 2400 * 2
        for i in range(0, len(pcm), step):
            await asyncio.sleep(0.002)
            if i == 0:
                self.emit("tts_first_pcm", provider="fake-tts")
            yield pcm[i:i + step]
        self.emit("tts_done", provider="fake-tts")

    async def warmup(self) -> None:
        pass

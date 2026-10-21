"""Interfaces des briques de la cascade STT -> LLM -> TTS (implémentations interchangeables)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class STT(Protocol):
    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        """Transcrit un segment de parole complet (Whisper n'est pas streaming)."""


class LLM(Protocol):
    def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Génère la réponse token par token (delta de texte)."""


class TTS(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Synthétise un texte en chunks PCM16 mono."""

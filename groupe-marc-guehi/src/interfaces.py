"""Interfaces des briques de la cascade STT -> LLM -> TTS (implémentations interchangeables)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


class STT(Protocol):
    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        """Transcrit un segment de parole complet (Whisper n'est pas streaming)."""


@dataclass(frozen=True)
class SearchNotice:
    """Émis dans le flux du LLM quand il décide de chercher sur internet : l'orchestrateur peut meubler l'attente."""
    query: str
    announce: str = ""  # phrase courte écrite par le LLM pour dire ce qu'il cherche (vide : accroche générique)


class LLM(Protocol):
    def stream(self, messages: list[dict]) -> AsyncIterator[str | SearchNotice]:
        """Génère la réponse token par token (delta de texte) ; peut intercaler un SearchNotice avant la réponse."""


class WebSearch(Protocol):
    async def search(self, query: str) -> str:
        """Résumé factuel et à jour de ce qu'on trouve sur internet pour la requête (texte brut, sans URL)."""


class TTS(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Synthétise un texte en chunks PCM16 mono."""

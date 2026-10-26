"""Transcriptions à ne pas traiter comme une question : vide, sans mot, annotation de bruit ou hallucination connue de Whisper."""
from __future__ import annotations

import re
from collections.abc import Iterable

ANNOTATION = re.compile(r"^\s*[\[(*♪♫][^\])*♪♫]*[\])*♪♫]\s*$")   # « [Musique] », « (rires) », « ♪ … ♪ » seuls : jamais de la parole


def normalize(text: str) -> str:
    """Minuscules, ponctuation retirée, espaces réduits (comparaison avec `stt.ignore_phrases`)."""
    return " ".join(re.sub(r"[^\w' ]", " ", text.lower().replace("’", "'")).split())


def noise_reason(text: str, ignore_phrases: Iterable[str] = ()) -> str | None:
    """Raison pour laquelle `text` doit être ignoré (`empty`, `no_words`, `annotation`, `hallucination`), sinon None.
    Seule l'égalité avec une phrase de la liste (après normalisation) compte : une phrase plus longue qui la contient est gardée."""
    if not text.strip():
        return "empty"
    if ANNOTATION.match(text):
        return "annotation"
    norm = normalize(text)
    if not any(c.isalnum() for c in norm):
        return "no_words"
    if norm in {normalize(p) for p in ignore_phrases}:
        return "hallucination"
    return None

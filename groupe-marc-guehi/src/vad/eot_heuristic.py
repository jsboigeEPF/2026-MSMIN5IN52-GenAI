"""Fin de tour adaptative (étude hors ligne, non branchée au serveur) : pauses internes d'un énoncé et heuristique
« la phrase semble-t-elle incomplète ? » sur le texte du STT spéculatif. Toutes les listes viennent de configs/campaign.yaml."""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.vad.endpointer import VadConfig
from src.vad.silero import FRAME_MS


@dataclass(frozen=True)
class Pause:
    start_ms: float       # début de la pause, depuis le début de la parole
    dur_ms: float
    frac: float           # position dans la parole (0 = début, 1 = fin)


def internal_pauses(probs: list[float], cfg: VadConfig, min_pause_ms: int) -> tuple[list[Pause], float]:
    """Pauses (silence VAD continu) entre la première et la dernière trame de parole. Retourne (pauses, durée de parole en ms)."""
    voiced = [i for i, p in enumerate(probs) if p >= cfg.threshold]
    if not voiced:
        return [], 0.0
    first, last = voiced[0], voiced[-1]
    total = (last - first + 1) * FRAME_MS
    out, run_start = [], None
    for i in range(first, last + 1):
        if probs[i] < cfg.neg_threshold:
            run_start = i if run_start is None else run_start
        elif run_start is not None:
            dur = (i - run_start) * FRAME_MS
            if dur >= min_pause_ms:
                out.append(Pause((run_start - first) * FRAME_MS, dur, (run_start - first) * FRAME_MS / total))
            run_start = None
    return out, total


def prefix_at(text: str, frac: float) -> str:
    """Texte probablement dit au moment d'une pause située à la fraction `frac` de la parole [H : répartition proportionnelle
    aux caractères, recalée sur la ponctuation voisine ; pas d'alignement mot à mot]."""
    words = text.split()
    ends, pos = [], 0
    for w in words:
        pos += len(w) + 1
        ends.append(pos / (len(text) + 1))
    best = min(range(len(words)), key=lambda i: abs(ends[i] - frac))
    for i in (best, best - 1, best + 1):   # une ponctuation voisine est une frontière de pause plus plausible
        if 0 <= i < len(words) and abs(ends[i] - frac) < 0.15 and re.search(r"[,;:.!?…]$", words[i]):
            best = i
            break
    return " ".join(words[:best + 1])


def looks_incomplete(text: str, last_words: set[str], trailing: tuple[str, ...], min_words: int) -> bool:
    t = text.strip()
    if not t:
        return True
    if t.endswith(trailing):
        return True
    words = re.findall(r"\w+", t.lower())
    return len(words) < min_words or words[-1] in last_words

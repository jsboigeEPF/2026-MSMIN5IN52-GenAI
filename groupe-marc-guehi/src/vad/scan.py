"""Balayage hors ligne d'un signal par le VAD + l'endpointer, avec les instants de chaque événement (campagne de mesures)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.vad.endpointer import Endpointer, VadConfig
from src.vad.silero import FRAME_BYTES, FRAME_MS, SileroVAD


@dataclass
class ScanEvent:
    kind: str       # speech_start | speech_end | discarded
    t_ms: float     # instant de détection (fin de la trame qui l'a déclenché)
    speech_ms: int = 0


def frame_probs(vad: SileroVAD, pcm16: bytes) -> list[float]:
    vad.reset()
    n = len(pcm16) // FRAME_BYTES
    return [vad.prob(pcm16[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]) for i in range(n)]


def scan(probs: list[float], cfg: VadConfig, strict_prob: float | None = None, barge_ms: int | None = None
         ) -> tuple[list[ScanEvent], float | None]:
    """Rejoue les probabilités dans l'endpointer. strict_prob : seuil relevé (agent en train de parler).
    Retourne (événements, instant du 1er barge-in) ; le barge-in est déclaré quand barge_ms de parole forte sont cumulés."""
    ep, events, barge_at = Endpointer(cfg), [], None
    dummy = b"\x00" * FRAME_BYTES
    for i, p in enumerate(probs):
        if strict_prob is not None and p < strict_prob:
            p = 0.0
        for ev in ep.push(dummy, p):
            events.append(ScanEvent(ev.kind, (i + 1) * FRAME_MS, ev.speech_ms))
        if barge_ms is not None and barge_at is None and ep.in_speech and ep.voiced_ms >= barge_ms:
            barge_at = (i + 1) * FRAME_MS
    return events, barge_at


def clip_probs(vad: SileroVAD, x: np.ndarray) -> list[float]:
    return frame_probs(vad, np.clip(np.round(x), -32768, 32767).astype(np.int16).tobytes())

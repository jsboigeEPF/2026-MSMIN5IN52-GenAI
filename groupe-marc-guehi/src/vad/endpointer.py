"""Détection début/fin de parole à partir des probabilités du VAD (logique pure, testable sans modèle)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class VadConfig:
    threshold: float = 0.5        # au-dessus : trame de parole
    neg_threshold: float = 0.35   # en dessous : trame de silence (hystérésis)
    min_speech_ms: int = 96       # parole continue requise pour déclencher le début
    end_silence_ms: int = 450     # silence requis pour déclarer la fin de tour
    preroll_ms: int = 300         # audio conservé avant le début détecté
    min_voiced_ms: int = 200      # en dessous, le segment est jeté (bruit)
    min_level_dbfs: float = -120.0  # porte de niveau : une trame plus faible que cela n'est jamais de la parole (-120 = désactivée)
    max_speech_s: float = 30.0
    frame_ms: int = 32


@dataclass
class Event:
    kind: str               # "speech_start" | "speech_end" | "discarded"
    pcm: bytes = b""        # audio du segment (speech_end)
    speech_ms: int = 0


class Endpointer:
    def __init__(self, cfg: VadConfig | None = None) -> None:
        self.cfg = cfg or VadConfig()
        self.reset()

    def reset(self) -> None:
        self._preroll: deque[bytes] = deque(maxlen=max(1, self.cfg.preroll_ms // self.cfg.frame_ms))
        self._frames: list[bytes] = []
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
        self._voiced = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def silence_ms(self) -> int:
        """Silence continu en cours (0 hors parole) : sert à lancer le STT avant la fin de tour."""
        return self._silence_run * self.cfg.frame_ms if self._in_speech else 0

    def snapshot(self) -> bytes:
        """Audio du segment en cours (pré-roll compris), silence final inclus."""
        return b"".join(self._frames)

    @property
    def voiced_ms(self) -> int:
        return self._voiced * self.cfg.frame_ms if self._in_speech else 0

    def push(self, frame: bytes, prob: float) -> list[Event]:
        c, out = self.cfg, []
        if not self._in_speech:
            self._preroll.append(frame)
            self._speech_run = self._speech_run + 1 if prob >= c.threshold else 0
            if self._speech_run * c.frame_ms >= c.min_speech_ms:
                self._in_speech = True
                self._frames = list(self._preroll)
                self._voiced = self._speech_run
                self._silence_run = 0
                out.append(Event("speech_start"))
            return out
        self._frames.append(frame)
        if prob >= c.threshold:
            self._voiced += 1
        self._silence_run = self._silence_run + 1 if prob < c.neg_threshold else 0
        too_long = len(self._frames) * c.frame_ms > c.max_speech_s * 1000
        if self._silence_run * c.frame_ms >= c.end_silence_ms or too_long:
            keep = len(self._frames) - max(0, self._silence_run - 3)  # on garde ~100 ms de silence final
            pcm = b"".join(self._frames[:keep])
            speech_ms = self._voiced * c.frame_ms
            out.append(Event("speech_end" if speech_ms >= c.min_voiced_ms else "discarded", pcm, speech_ms))
            self.reset()
        return out

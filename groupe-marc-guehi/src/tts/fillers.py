"""Phrases d'accroche pré-enregistrées : jouées dès la fin de parole pour masquer l'attente de la vraie réponse."""
from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.audio.pcm import SAMPLE_RATE_OUT, read_wav, wav_bytes
from src.interfaces import TTS


@dataclass(frozen=True)
class Filler:
    text: str
    pcm: bytes  # PCM16 24 kHz, silences de début et de fin rognés
    dur: float


def trim_silence(pcm: bytes, rate: int = SAMPLE_RATE_OUT, keep_ms: int = 30, rel_thresh: float = 0.04) -> bytes:
    """Rogne les silences de début/fin (le TTS en ajoute ; en début de phrase ils comptent dans la latence perçue)."""
    x = np.frombuffer(pcm, np.int16)
    step = int(rate * 0.01)
    n = len(x) // step
    if n == 0:
        return pcm
    rms = np.sqrt((x[:n * step].astype(np.float64).reshape(n, step) ** 2).mean(1))
    voiced = np.where(rms > rel_thresh * rms.max())[0]
    if len(voiced) == 0:
        return pcm
    keep = int(keep_ms / 10)
    a, b = max(0, voiced[0] - keep), min(n, voiced[-1] + 1 + keep)
    return x[a * step:b * step].tobytes()


class FillerBank:
    """Choisit une accroche dont la durée est proche de l'attente moyenne observée (peu de silence après l'accroche),
    au hasard parmi les 3 plus proches et jamais deux fois de suite la même."""

    def __init__(self, fillers: list[Filler], expected_wait_s: float = 1.6, rng: random.Random | None = None) -> None:
        if not fillers:
            raise ValueError("aucune accroche")
        self.fillers, self.expected_wait_s = fillers, expected_wait_s
        self._rng, self._last = rng or random.Random(), None

    def pick(self) -> Filler:
        pool = [f for f in self.fillers if f is not self._last] or self.fillers
        ranked = sorted(pool, key=lambda f: abs(f.dur - self.expected_wait_s))
        self._last = self._rng.choice(ranked[:3])
        return self._last

    def observe(self, wait_s: float) -> None:
        """wait_s : délai réel entre l'envoi de l'accroche et l'arrivée du premier audio de la vraie réponse."""
        self.expected_wait_s = 0.7 * self.expected_wait_s + 0.3 * wait_s


async def load_fillers(tts: TTS, texts: list[str], cache_dir: Path, tag: str, concurrency: int = 3) -> list[Filler]:
    """Charge les accroches depuis le cache disque (data/fillers/<tag>/), les synthétise sinon."""
    folder = cache_dir / tag
    folder.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(text: str) -> Filler:
        path = folder / (hashlib.md5(text.encode()).hexdigest()[:10] + ".wav")
        if path.exists():
            pcm, _ = read_wav(str(path))
        else:
            async with sem:
                pcm = trim_silence(b"".join([c async for c in tts.stream(text)]))
            path.write_bytes(wav_bytes(pcm, SAMPLE_RATE_OUT))
        return Filler(text, pcm, len(pcm) / (2 * SAMPLE_RATE_OUT))

    return list(await asyncio.gather(*[one(t) for t in texts]))

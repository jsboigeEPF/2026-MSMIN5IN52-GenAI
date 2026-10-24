"""Écho synthétique : la sortie de l'agent est réinjectée dans le micro avec un retard et un gain fixés.
Limites (écho simulé ≠ réel) : un seul trajet retardé, ni réverbération de pièce, ni non-linéarité du haut-parleur,
ni annulation d'écho du navigateur ; le niveau est relatif à la parole de référence de la campagne."""
from __future__ import annotations

import numpy as np

MIC_RATE = 16000


def echo_gain(echo_db: float, ref_rms: float, agent_rms: float) -> float:
    """Gain linéaire pour que l'écho ait un niveau de `echo_db` dB par rapport à la parole de référence (`ref_rms`),
    pour un signal d'agent de niveau `agent_rms`. 0 dB = écho aussi fort que la parole de référence."""
    return 10 ** (echo_db / 20) * ref_rms / (agent_rms or 1.0)


class EchoPath:
    """Trajet haut-parleur -> micro sur l'horloge d'échantillons du micro (16 kHz). `feed` place l'audio de l'agent à la suite de
    ce qui est déjà programmé (la lecture est séquentielle), `read` rend l'écho à capter dans [pos, pos+n)."""

    def __init__(self, delay_ms: float, gain: float, rate: int = MIC_RATE) -> None:
        self.delay, self.gain = int(delay_ms * rate / 1000), gain
        self._segs: list[tuple[int, np.ndarray]] = []
        self._end = 0

    def feed(self, samples: np.ndarray, pos: int) -> None:
        """L'agent émet `samples` à partir de l'instant `pos` (ou dès que la lecture en cours est finie)."""
        start = max(pos, self._end)
        self._segs.append((start + self.delay, np.asarray(samples, dtype=float) * self.gain))
        self._end = start + len(samples)

    def read(self, pos: int, n: int) -> np.ndarray:
        out = np.zeros(n)
        for start, x in self._segs:
            a, b = max(start, pos), min(start + len(x), pos + n)
            if b > a:
                out[a - pos:b - pos] += x[a - start:b - start]
        self._segs = [(s, x) for s, x in self._segs if s + len(x) > pos + n]
        return out

    def stop(self, pos: int) -> None:
        """Le haut-parleur se tait à l'instant `pos` : l'écho déjà en route (le retard) arrive encore, rien de plus."""
        limit = pos + self.delay
        self._segs = [(s, x[:limit - s]) for s, x in self._segs if s < limit]
        self._end = min(self._end, pos)

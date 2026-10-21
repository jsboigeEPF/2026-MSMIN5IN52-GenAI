"""Bruits contrôlés à rapport signal/bruit (SNR) fixé. Tout est déterministe via un générateur numpy passé en argument."""
from __future__ import annotations

import numpy as np

INT16_MAX = 32767


def to_f(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, np.int16).astype(np.float64)


def to_pcm(x: np.ndarray) -> bytes:
    return np.clip(np.round(x), -32768, INT16_MAX).astype(np.int16).tobytes()


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def speech_rms(x: np.ndarray, frame: int = 320, rel_thresh: float = 0.1) -> float:
    """RMS mesuré sur les seules trames de parole (silences exclus) : c'est la référence du SNR."""
    n = len(x) // frame
    if n == 0:
        return rms(x)
    frames = x[:n * frame].reshape(n, frame)
    r = np.sqrt(np.mean(frames ** 2, axis=1))
    voiced = r > rel_thresh * r.max()
    return float(np.sqrt(np.mean(frames[voiced] ** 2))) if voiced.any() else rms(x)


def white(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n)


def babble(clips: list[np.ndarray], n: int, rng: np.random.Generator, layers: int = 4) -> np.ndarray:
    """Murmure de foule : somme de `layers` pistes, chacune faite de phrases tirées au hasard avec des pauses."""
    if not clips:
        raise ValueError("babble : aucun clip")
    out = np.zeros(n)
    for _ in range(layers):
        track: list[np.ndarray] = []
        total = 0
        while total < n + 16000:
            clip = clips[int(rng.integers(len(clips)))]
            gap = np.zeros(int(rng.uniform(0.1, 0.8) * 16000))
            track += [clip / (rms(clip) or 1.0), gap]
            total += len(clip) + len(gap)
        t = np.concatenate(track)
        start = int(rng.integers(0, max(1, len(t) - n)))
        out += t[start:start + n]
    return out


def noise_signal(kind: str, n: int, rng: np.random.Generator, clips: list[np.ndarray] | None = None) -> np.ndarray:
    if kind == "white":
        return white(n, rng)
    if kind == "babble":
        return babble(clips or [], n, rng)
    raise ValueError(f"type de bruit inconnu : {kind}")


def scale_to_snr(noise: np.ndarray, ref_speech_rms: float, snr_db: float) -> np.ndarray:
    """Met le bruit à l'échelle pour que 20·log10(rms_parole / rms_bruit) = snr_db."""
    target = ref_speech_rms / (10 ** (snr_db / 20))
    return noise * (target / (rms(noise) or 1.0))


def mix(speech: np.ndarray, noise: np.ndarray, ref_speech_rms: float, snr_db: float) -> np.ndarray:
    """Parole + bruit (le bruit couvre aussi les silences, comme en situation réelle)."""
    n = len(speech)
    noise = np.resize(noise, n)
    return speech + scale_to_snr(noise, ref_speech_rms, snr_db)

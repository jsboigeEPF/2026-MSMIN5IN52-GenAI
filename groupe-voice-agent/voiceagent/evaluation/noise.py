"""Noise mixing: requested SNR survives a common anti-clipping gain."""

import numpy as np

from voiceagent.domain import Samples
from voiceagent.vad import rms


def generate_noise(kind: str, length: int, rng: np.random.Generator) -> Samples:
    if length <= 0:
        raise ValueError("Longueur du bruit invalide")
    white = rng.normal(size=length)
    if kind == "white":
        return white.astype(np.float32)
    if kind == "pink":
        spectrum = np.fft.rfft(white)
        frequencies = np.fft.rfftfreq(length)
        scale = np.zeros_like(frequencies)
        scale[1:] = 1 / np.sqrt(frequencies[1:])
        return np.fft.irfft(spectrum * scale, n=length).astype(np.float32)
    raise ValueError(f"Bruit inconnu : {kind}")


def mix_noise(
    clean: Samples, noise: Samples, snr_db: float | None, speech_samples: int | None = None
) -> tuple[Samples, dict]:
    if snr_db is None:
        return clean.copy(), {"snr_db": None, "gain": 1.0}
    if not np.isfinite(snr_db) or not len(clean) or not len(noise):
        raise ValueError("Signal, bruit ou SNR invalide")
    noise = np.resize(noise, clean.shape).astype(np.float32)
    count = len(clean) if speech_samples is None else speech_samples
    if not 0 < count <= len(clean):
        raise ValueError("Intervalle de mesure SNR invalide")
    signal_rms = rms(clean[:count])
    noise_rms = rms(noise[:count])
    if signal_rms < 1e-10 or noise_rms < 1e-10:
        raise ValueError("SNR indéfini pour un signal ou bruit nul")
    scaled_noise = noise * (signal_rms / (noise_rms * 10 ** (snr_db / 20)))
    mixed = clean + scaled_noise
    peak = float(np.max(np.abs(mixed)))
    gain = min(1.0, 0.98 / max(peak, 1e-10))
    return (mixed * gain).astype(np.float32), {"snr_db": snr_db, "gain": gain}

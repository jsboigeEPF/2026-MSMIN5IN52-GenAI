"""Utilitaires PCM16 mono little-endian."""
from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE_OUT = 24000  # fréquence de lecture de l'agent
SAMPLE_RATE_STT = 16000  # fréquence attendue par Whisper


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def read_wav(path: str) -> tuple[bytes, int]:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("WAV attendu : mono, 16 bits")
        return w.readframes(w.getnframes()), w.getframerate()


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Rééchantillonnage linéaire (suffisant pour la parole ; pas de filtre anti-repliement)."""
    if src == dst or not pcm:
        return pcm
    x = np.frombuffer(pcm, np.int16).astype(np.float64)
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16).tobytes()


def rms_dbfs(pcm: bytes) -> float:
    """Niveau efficace en dB par rapport à la pleine échelle (0 dBFS = ±32768) ; -120 pour un silence numérique."""
    if not pcm:
        return -120.0
    x = np.frombuffer(pcm, np.int16).astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(x * x)))
    return 20 * np.log10(rms) if rms > 1e-6 else -120.0


def silence(ms: int, rate: int) -> bytes:
    return b"\x00\x00" * int(rate * ms / 1000)


def duration_s(pcm: bytes, rate: int) -> float:
    return len(pcm) / (2 * rate)

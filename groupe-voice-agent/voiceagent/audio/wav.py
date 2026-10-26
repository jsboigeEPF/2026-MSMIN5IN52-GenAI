import math
import wave
from pathlib import Path

import numpy as np

from voiceagent.domain import Samples


def resample(samples: Samples, source_rate: int, target_rate: int) -> Samples:
    if source_rate == target_rate:
        return samples
    from scipy.signal import resample_poly

    factor = math.gcd(source_rate, target_rate)
    return resample_poly(samples, target_rate // factor, source_rate // factor).astype(
        np.float32
    )


def read_wav(path: Path, target_rate: int = 16000) -> Samples:
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2:
            raise ValueError(f"WAV PCM16 requis : {path}")
        channels = stream.getnchannels()
        rate = stream.getframerate()
        raw = stream.readframes(stream.getnframes())
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    if not len(samples):
        raise ValueError(f"WAV vide : {path}")
    samples = samples.reshape(-1, channels).mean(axis=1)
    return resample(samples, rate, target_rate)


def write_wav(path: Path, samples: Samples, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.round(np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(data.tobytes())

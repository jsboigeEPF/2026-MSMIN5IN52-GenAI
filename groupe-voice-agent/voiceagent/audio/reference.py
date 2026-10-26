"""Small playout timeline for controlled echo injection in E3/E4."""

from collections import deque

import numpy as np


class PlaybackReference:
    def __init__(self):
        self.segments = deque(maxlen=6)
        self.first_audio_ns = None

    def add(self, start_ns: int, samples, rate: int) -> None:
        self.first_audio_ns = start_ns
        self.segments.append([start_ns, samples, rate, start_ns + len(samples) / rate * 1e9])

    def stop(self, t_ns: int) -> None:
        for segment in self.segments:
            segment[3] = min(segment[3], t_ns)

    def sample(self, end_ns: int, delay_ms: float = 30, attenuation_db: float = -10):
        times = end_ns - delay_ms * 1e6 - (512 - np.arange(512)) / 16000 * 1e9
        output = np.zeros(512, dtype=np.float32)
        for start, samples, rate, cutoff in self.segments:
            positions = (times - start) * rate / 1e9
            valid = (positions >= 0) & (positions < len(samples) - 1) & (times < cutoff)
            selected = positions[valid]
            left = selected.astype(np.int64)
            weight = selected - left
            output[valid] += samples[left] * (1 - weight) + samples[left + 1] * weight
        return output * 10 ** (attenuation_db / 20)

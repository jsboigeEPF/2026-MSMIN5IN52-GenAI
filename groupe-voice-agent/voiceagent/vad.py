"""Turn detector independent of Silero, with pre-roll and validated barge-in."""

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from voiceagent.config import VadConfig
from voiceagent.domain import Frame, Utterance


@dataclass(frozen=True)
class Detection:
    kind: str
    t_ns: int
    utterance: Utterance | None = None


def rms(samples) -> float:
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


class TurnDetector:
    def __init__(self, config: VadConfig, block_ms: int = 32):
        self.config = config
        self.block_ms = block_ms
        self._pre: deque[Frame] = deque(maxlen=math.ceil(config.preroll_ms / block_ms))
        self.reset()

    def reset(self) -> None:
        self._pre.clear()
        self._frames: list[Frame] = []
        self._candidate: list[Frame] = []
        self._active = False
        self._start_ns = 0
        self._last_voice_ns = 0
        self._voiced_ms = 0

    def feed(
        self,
        frame: Frame,
        probability: float,
        *,
        busy: bool = False,
        reference_rms: float = 0.0,
        half_duplex: bool = False,
    ) -> list[Detection]:
        if half_duplex and busy:
            self.reset()
            return []
        config = self.config
        threshold = config.barge_threshold if busy and not self._active else config.threshold
        voiced = probability > threshold
        if busy and not self._active and config.energy_gate and reference_rms > 1e-6:
            echo = reference_rms * 10 ** (config.echo_attenuation_db / 20)
            voiced = voiced and rms(frame.samples) > echo * 10 ** (config.energy_margin_db / 20)

        if not self._active:
            if not voiced:
                for candidate in self._candidate:
                    self._pre.append(candidate)
                self._candidate.clear()
                self._pre.append(frame)
                return []
            self._candidate.append(frame)
            required_ms = config.barge_ms if busy else config.start_ms
            if len(self._candidate) * self.block_ms < required_ms:
                return []
            self._active = True
            self._start_ns = self._candidate[0].end_ns - self.block_ms * 1_000_000
            self._frames = list(self._pre) + self._candidate
            self._voiced_ms = len(self._candidate) * self.block_ms
            self._candidate = []
            self._last_voice_ns = frame.end_ns
            kind = "barge_start" if busy else "speech_start"
            return [Detection(kind, self._start_ns)]

        self._frames.append(frame)
        if voiced:
            self._last_voice_ns = frame.end_ns
            self._voiced_ms += self.block_ms
        silence_ms = (frame.end_ns - self._last_voice_ns) / 1_000_000
        duration_s = (frame.end_ns - self._start_ns) / 1_000_000_000
        if silence_ms < config.silence_ms and duration_s < config.max_speech_s:
            return []

        # Remove endpoint silence from STT input, preserving candidate + pre-roll.
        frames = [item.samples for item in self._frames if item.end_ns <= self._last_voice_ns]
        utterance = Utterance(
            np.concatenate(frames), self._start_ns, self._last_voice_ns, frame.end_ns
        )
        accepted = self._voiced_ms >= config.min_speech_ms
        self.reset()
        if accepted:
            return [Detection("endpoint", frame.end_ns, utterance)]
        return [Detection("discarded", frame.end_ns)]

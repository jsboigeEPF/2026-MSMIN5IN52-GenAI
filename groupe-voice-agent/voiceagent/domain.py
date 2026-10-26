"""Small shared value objects and replaceable backend contracts."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

Samples = NDArray[np.float32]


class State(StrEnum):
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class Frame:
    samples: Samples
    end_ns: int


@dataclass(frozen=True)
class Utterance:
    samples: Samples
    start_ns: int
    end_ns: int
    endpoint_ns: int


@dataclass(frozen=True)
class AudioChunk:
    text: str
    samples: Samples
    sample_rate: int


class Transcriber(Protocol):
    def transcribe(self, samples: Samples) -> str: ...


class LanguageModel(Protocol):
    def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> AudioChunk: ...


class Player(Protocol):
    audible: bool
    reference_rms: float

    async def play(self, chunk: AudioChunk, turn: int) -> bool: ...

    async def stop(self) -> int: ...

    async def close(self) -> None: ...

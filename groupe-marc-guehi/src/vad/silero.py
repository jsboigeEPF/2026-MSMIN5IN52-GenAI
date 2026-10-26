"""VAD Silero (ONNX, CPU) : probabilité de parole par trame de 512 échantillons à 16 kHz (32 ms)."""
from __future__ import annotations

import numpy as np
import torch
from silero_vad import load_silero_vad

FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_MS = 32


class SileroVAD:
    """Le modèle est à état (RNN) : une instance par conversation active (prototype mono-utilisateur)."""

    def __init__(self) -> None:
        self.model = load_silero_vad(onnx=True)

    def reset(self) -> None:
        self.model.reset_states()

    def prob(self, frame: bytes) -> float:
        x = np.frombuffer(frame, np.int16).astype(np.float32) / 32768.0
        return float(self.model(torch.from_numpy(x), 16000))

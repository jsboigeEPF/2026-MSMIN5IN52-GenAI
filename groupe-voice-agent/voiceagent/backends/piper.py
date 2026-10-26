from pathlib import Path

import numpy as np

from voiceagent.config import TtsConfig
from voiceagent.domain import AudioChunk


class Piper:
    def __init__(self, config: TtsConfig):
        from piper import PiperVoice

        path = Path(config.piper_model)
        if not path.is_file():
            raise FileNotFoundError(f"Voix Piper absente : {path}. Voir docs/INSTALLATION.md.")
        self._voice = PiperVoice.load(str(path), use_cuda=False)
        self.speed = config.speed

    def synthesize(self, text: str) -> AudioChunk:
        from piper import SynthesisConfig

        chunks = []
        rate = self._voice.config.sample_rate
        config = SynthesisConfig(length_scale=1 / self.speed)
        for chunk in self._voice.synthesize(text, syn_config=config):
            samples = np.frombuffer(chunk.audio_int16_bytes, dtype="<i2")
            chunks.append(samples.astype(np.float32) / 32768.0)
            rate = chunk.sample_rate
        if not chunks:
            raise RuntimeError("Piper n'a produit aucun audio")
        return AudioChunk(text, np.concatenate(chunks), rate)

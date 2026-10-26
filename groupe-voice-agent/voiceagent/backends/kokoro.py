from pathlib import Path

import numpy as np

from voiceagent.config import TtsConfig
from voiceagent.domain import AudioChunk


class Kokoro:
    def __init__(self, config: TtsConfig):
        from kokoro import KModel, KPipeline

        self.config = config
        root = Path(config.repo)
        if root.is_dir():
            model = (
                KModel(
                    config=str(root / "config.json"),
                    model=str(root / "kokoro-v1_0.pth"),
                )
                .to(config.device)
                .eval()
            )
            self._pipeline = KPipeline(lang_code="f", model=model, device=config.device)
            voice = root / "voices" / f"{config.voice}.pt"
            if not voice.is_file():
                raise FileNotFoundError(
                    f"Voix absente du snapshot : {voice}. "
                    "Modifier config.yaml puis refaire prepare."
                )
            self._voice = str(voice)
        else:
            self._pipeline = KPipeline(lang_code="f", repo_id=config.repo, device=config.device)
            self._voice = config.voice

    def synthesize(self, text: str) -> AudioChunk:
        chunks = []
        for _, _, audio in self._pipeline(text, voice=self._voice, speed=self.config.speed):
            if audio is not None:
                chunks.append(audio.detach().cpu().numpy().astype(np.float32))
        if not chunks:
            raise RuntimeError("Kokoro n'a produit aucun audio")
        return AudioChunk(text, np.concatenate(chunks), 24000)

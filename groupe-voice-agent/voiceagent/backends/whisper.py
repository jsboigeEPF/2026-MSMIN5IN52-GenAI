from voiceagent.config import SttConfig
from voiceagent.domain import Samples


class Whisper:
    def __init__(self, config: SttConfig):
        from faster_whisper import WhisperModel

        self.config = config
        self._model = WhisperModel(
            config.model,
            device=config.device,
            compute_type=config.compute_type,
            num_workers=1,
            cpu_threads=4,
            revision=config.revision,
        )

    def transcribe(self, samples: Samples) -> str:
        segments, _ = self._model.transcribe(
            samples,
            language="fr",
            beam_size=self.config.beam_size,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            vad_filter=False,
            temperature=0.0,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

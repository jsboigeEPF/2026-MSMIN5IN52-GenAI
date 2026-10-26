"""Typed, validated configuration; unknown settings fail early."""

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    output_rate: int = 24000
    block_ms: int = 32
    output_block_ms: int = 20
    input_device: str | int | None = None
    output_device: str | int | None = None
    capture_queue: int = 64
    half_duplex: bool = False


@dataclass
class VadConfig:
    threshold: float = 0.5
    barge_threshold: float = 0.7
    start_ms: int = 64
    barge_ms: int = 300
    silence_ms: int = 500
    preroll_ms: int = 200
    min_speech_ms: int = 300
    max_speech_s: float = 30.0
    energy_gate: bool = True
    echo_attenuation_db: float = -10.0
    energy_margin_db: float = 6.0


@dataclass
class SttConfig:
    model: str = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    revision: str = "main"
    device: str = "cuda"
    compute_type: str = "int8_float16"
    beam_size: int = 1


@dataclass
class LlmConfig:
    url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:3b-instruct-q4_K_M"
    expected_digest: str | None = None
    timeout_s: float = 60.0
    history_turns: int = 6
    num_ctx: int = 2048
    max_tokens: int = 256
    temperature: float = 0.2
    max_sentence_chars: int = 180
    system_prompt: str = (
        "Tu es un assistant vocal français. Réponds en français, clairement et brièvement. "
        "Fais des phrases courtes, sans Markdown. Réponds directement à la question."
    )


@dataclass
class TtsConfig:
    engine: str = "kokoro"
    device: str = "cuda"
    voice: str = "ff_siwis"
    repo: str = "hexgrad/Kokoro-82M"
    revision: str = "main"
    piper_model: str = "models/fr_FR-siwis-medium.onnx"
    speed: float = 1.0


@dataclass
class Config:
    seed: int = 42
    runs_dir: str = "runs"
    turn_timeout_s: float = 120.0
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)

    def validate(self) -> None:
        if self.audio.sample_rate != 16000 or self.audio.block_ms != 32:
            raise ValueError(
                "Silero exige ici 16 kHz et des blocs de 512 échantillons (32 ms)."
            )
        if not 0 < self.vad.threshold <= self.vad.barge_threshold < 1:
            raise ValueError("Seuils VAD invalides.")
        if min(self.vad.start_ms, self.vad.barge_ms, self.vad.silence_ms) <= 0:
            raise ValueError("Les durées VAD doivent être positives.")
        if self.vad.max_speech_s <= self.vad.min_speech_ms / 1000:
            raise ValueError("max_speech_s doit dépasser min_speech_ms.")
        if self.vad.preroll_ms < 0 or self.vad.min_speech_ms < 0:
            raise ValueError("Les durées VAD ne peuvent pas être négatives.")
        if self.audio.output_rate <= 0 or self.audio.output_block_ms <= 0:
            raise ValueError("Configuration de sortie audio invalide.")
        if self.audio.capture_queue < 2 or self.llm.history_turns < 1:
            raise ValueError("Files et historique doivent avoir une capacité positive.")
        if self.llm.max_sentence_chars < 20 or self.tts.speed <= 0:
            raise ValueError("Taille de phrase ou vitesse TTS invalide.")
        if self.turn_timeout_s <= 0 or self.llm.timeout_s <= 0:
            raise ValueError("Les délais doivent être positifs.")
        if self.tts.engine not in {"kokoro", "piper"}:
            raise ValueError("Moteur TTS attendu : kokoro ou piper.")

    def as_dict(self) -> dict:
        return asdict(self)


def load_config(path: Path | None = None) -> Config:
    import yaml

    raw = {} if path is None else yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("La configuration doit être une table YAML.")
    section_types = {
        "audio": AudioConfig,
        "vad": VadConfig,
        "stt": SttConfig,
        "llm": LlmConfig,
        "tts": TtsConfig,
    }
    values = dict(raw)
    try:
        for key, section_type in section_types.items():
            if key in values:
                values[key] = section_type(**values[key])
        config = Config(**values)
        config.validate()
    except (TypeError, AttributeError) as exc:
        raise ValueError(f"Configuration inconnue ou mal typée : {exc}") from exc
    return config

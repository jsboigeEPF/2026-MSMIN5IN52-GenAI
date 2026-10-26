"""Réglages et dépendances d'une `ConversationSession` (valeurs lues dans configs/base.yaml, jamais codées en dur)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.interfaces import LLM, STT, TTS
from src.metrics.recorder import Recorder
from src.tts.fillers import FillerBank
from src.vad.endpointer import Endpointer
from src.vad.silero import SileroVAD


@dataclass(frozen=True)
class SessionConfig:
    max_history: int
    strict_after_s: float
    barge_min_ms: int
    barge_prob: float
    playback_margin_s: float
    carry_gap_ms: int
    failsafe_margin_s: float
    interrupt_ack_timeout_s: float
    strict_transitions: bool
    filler_min_speech_ms: int
    filler_hint: str
    first_min: int
    rest_min: int
    speculative_ms: int
    text_fallback_after: int
    search_wait_after_s: float
    ignore_phrases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, cfg: dict) -> SessionConfig:
        """`cfg` = contenu de configs/base.yaml. `speculative_ms`, `fallback_to_text_after`, `search_wait_after_s` : 0 = désactivé."""
        s, f, t, llm, stt = cfg["session"], cfg.get("filler", {}), cfg["tts"], cfg["llm"], cfg.get("stt", {})
        return cls(
            max_history=s["max_history_messages"], strict_after_s=s["strict_after_s"],
            barge_min_ms=s["barge_in_min_speech_ms"], barge_prob=s["barge_in_min_prob"],
            playback_margin_s=s["playback_margin_s"], carry_gap_ms=s["carry_gap_ms"],
            failsafe_margin_s=s["failsafe_margin_s"], interrupt_ack_timeout_s=s["interrupt_ack_timeout_s"],
            strict_transitions=s["strict_transitions"], filler_min_speech_ms=f.get("min_speech_ms", 0),
            filler_hint=llm["filler_hint"], first_min=t["first_chunk_min_chars"], rest_min=t["min_chunk_chars"],
            speculative_ms=stt.get("speculative_ms", 0), text_fallback_after=stt.get("fallback_to_text_after", 0),
            search_wait_after_s=f.get("search_wait_after_s", 0.0), ignore_phrases=tuple(stt.get("ignore_phrases", ())))


@dataclass
class SessionDeps:
    """Briques et canaux de sortie injectés (réels, factices ou de test)."""
    stt: STT
    llm: LLM
    tts: TTS
    vad: SileroVAD
    endpointer: Endpointer
    rec: Recorder
    system_prompt: str
    send_json: Callable[[dict], Awaitable[None]]
    send_audio: Callable[[bytes], Awaitable[None]]
    fillers: FillerBank | None = None
    search_fillers: FillerBank | None = None
    wait_fillers: FillerBank | None = None

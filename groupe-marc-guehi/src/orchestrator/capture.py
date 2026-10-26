"""Capture du micro : trames -> VAD -> endpointer, seuil relevé quand l'agent parle, STT spéculatif."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from src.audio.pcm import rms_dbfs
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.orchestrator.tasks import TurnTasks
from src.vad.endpointer import Event
from src.vad.silero import FRAME_BYTES

MIC_RATE = 16000


@dataclass
class Signal:
    kind: str                  # "speech_start" | "speech_end" | "discarded" | "barge_in"
    event: Event | None = None


class Capture:
    def __init__(self, deps: SessionDeps, cfg: SessionConfig, tasks: TurnTasks, agent_active: Callable[[], bool]) -> None:
        self.deps, self.cfg, self.tasks, self._agent_active = deps, cfg, tasks, agent_active
        self.strict_until = 0.0     # monotonic : jusqu'à cet instant (hors parole) le seuil reste relevé (écho résiduel)
        self._buf = b""

    def clear(self) -> None:
        """Repart d'un endpointer et d'un tampon vides (le VAD garde son état)."""
        self.deps.endpointer.reset()
        self._buf = b""

    def reset(self, strict_for_s: float | None = None) -> None:
        self.deps.vad.reset()
        self.clear()
        if strict_for_s is not None:
            self.strict_until = time.monotonic() + strict_for_s

    def frames(self, data: bytes) -> Iterator[Signal]:
        """Octets PCM16 16 kHz mono. Le VAD tourne en permanence ; pendant que l'agent réfléchit ou parle, seuil relevé et durée
        minimale plus longue pour ne pas se couper sur son propre écho. Générateur : l'appelant agit entre deux signaux."""
        ep = self.deps.endpointer
        self._buf += data
        while len(self._buf) >= FRAME_BYTES:
            frame, self._buf = self._buf[:FRAME_BYTES], self._buf[FRAME_BYTES:]
            prob = self.deps.vad.prob(frame)  # appelé à chaque trame : le VAD garde son état même si la trame est ignorée
            if rms_dbfs(frame) < ep.cfg.min_level_dbfs:
                prob = 0.0
            strict = self._agent_active() or (time.monotonic() < self.strict_until and not ep.in_speech)
            if strict and prob < self.cfg.barge_prob:
                prob = 0.0
            for ev in ep.push(frame, prob):
                yield Signal(ev.kind, ev)
            self._speculate()
            if self._agent_active() and ep.in_speech and ep.voiced_ms >= self.cfg.barge_min_ms:
                yield Signal("barge_in")

    def _speculate(self) -> None:
        """STT spéculatif : on transcrit dès `speculative_ms` de silence, sans attendre la fin de tour. Si la parole reprend,
        le résultat est jeté ; sinon il est déjà (en partie) prêt quand la fin de tour est déclarée."""
        if not self.cfg.speculative_ms:
            return
        ep = self.deps.endpointer
        silence = ep.silence_ms
        if self.tasks.spec and (not ep.in_speech or silence < self.cfg.speculative_ms):
            self.tasks.cancel_spec()
        if self.tasks.spec is None and ep.in_speech and not self._agent_active() and silence >= self.cfg.speculative_ms:
            self.deps.rec("stt_speculative_start", silence_ms=silence)
            self.tasks.spawn_spec(self.deps.stt.transcribe(ep.snapshot(), MIC_RATE))

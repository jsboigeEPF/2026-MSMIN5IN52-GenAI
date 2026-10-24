"""Balayage hors ligne d'un signal par le VAD + l'endpointer, avec les instants de chaque événement (campagne de mesures)."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from src.audio.echo import MIC_RATE, EchoPath
from src.vad.endpointer import Endpointer, VadConfig
from src.vad.silero import FRAME_BYTES, FRAME_MS, SileroVAD


@dataclass
class ScanEvent:
    kind: str       # speech_start | speech_end | discarded
    t_ms: float     # instant de détection (fin de la trame qui l'a déclenché)
    speech_ms: int = 0


def frame_probs(vad: SileroVAD, pcm16: bytes) -> list[float]:
    vad.reset()
    n = len(pcm16) // FRAME_BYTES
    return [vad.prob(pcm16[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]) for i in range(n)]


def scan(probs: list[float], cfg: VadConfig, strict_prob: float | None = None, barge_ms: int | None = None,
         strict_until_ms: float | None = None) -> tuple[list[ScanEvent], float | None]:
    """Rejoue les probabilités dans l'endpointer. strict_prob : seuil relevé (agent en train de parler), jusqu'à
    `strict_until_ms` si fourni (l'agent s'est tu), sinon sur tout le signal.
    Retourne (événements, instant du 1er barge-in) ; le barge-in est déclaré quand barge_ms de parole forte sont cumulés."""
    ep, events, barge_at = Endpointer(cfg), [], None
    dummy = b"\x00" * FRAME_BYTES
    for i, p in enumerate(probs):
        if strict_prob is not None and p < strict_prob and (strict_until_ms is None or (i + 1) * FRAME_MS <= strict_until_ms):
            p = 0.0
        for ev in ep.push(dummy, p):
            events.append(ScanEvent(ev.kind, (i + 1) * FRAME_MS, ev.speech_ms))
        if barge_ms is not None and barge_at is None and ep.in_speech and ep.voiced_ms >= barge_ms:
            barge_at = (i + 1) * FRAME_MS
    return events, barge_at


def clip_probs(vad: SileroVAD, x: np.ndarray) -> list[float]:
    return frame_probs(vad, np.clip(np.round(x), -32768, 32767).astype(np.int16).tobytes())


# ---------- écho simulé : l'agent parle, le micro recapte sa voix (± l'utilisateur par-dessus) ----------
@dataclass(frozen=True)
class EchoCase:
    agent: np.ndarray               # signal de l'agent (proxy : voix de synthèse), 16 kHz
    agent_start: int                # échantillon où l'agent commence à parler
    gain: float | None              # gain linéaire de l'écho ; None = pas d'écho (contrôle)
    delay_ms: float
    user: np.ndarray | None = None  # parole de l'utilisateur par-dessus (None : écho seul)
    user_start: int = 0
    tail: int = 2 * MIC_RATE        # silence final

    @property
    def delay_samples(self) -> int:
        return int(self.delay_ms * MIC_RATE / 1000)

    @property
    def user_start_ms(self) -> float:
        return self.user_start / MIC_RATE * 1000

    @property
    def user_end_ms(self) -> float:
        return (self.user_start + (0 if self.user is None else len(self.user))) / MIC_RATE * 1000

    def render(self, agent_stop: int | None = None) -> np.ndarray:
        """Signal du micro. `agent_stop` : échantillon où l'agent se tait (après un barge-in) ; son écho s'éteint après le retard."""
        end = max(self.agent_start + len(self.agent) + self.delay_samples, self.user_start + (0 if self.user is None else len(self.user)))
        x = np.zeros(end + self.tail)
        if self.gain is not None:
            path = EchoPath(self.delay_ms, self.gain)
            path.feed(self.agent, self.agent_start)
            if agent_stop is not None:
                path.stop(agent_stop)
            x += path.read(0, len(x))
        if self.user is not None:
            x[self.user_start:self.user_start + len(self.user)] += self.user
        return x


def classify_barge(at_ms: float | None, case: EchoCase, detect_max_ms: float) -> str:
    """false : déclenché avant la parole de l'utilisateur (ou sans utilisateur) ; ok : dans les temps ; late : trop tard ;
    missed : jamais ; none : écho seul sans déclenchement (cas correct)."""
    if at_ms is None:
        return "none" if case.user is None else "missed"
    if case.user is None or at_ms < case.user_start_ms:
        return "false"
    return "ok" if at_ms - case.user_start_ms <= detect_max_ms else "late"


def end_of_turn_after_barge(vad: SileroVAD, case: EchoCase, vcfg: VadConfig, strict_prob: float, at_ms: float,
                            agent_stop_ms: float, end_silences: list[int]) -> dict[int, dict]:
    """Après un barge-in détecté à `at_ms`, l'agent se tait `agent_stop_ms` plus tard (son écho s'éteint après le retard) et le
    seuil relevé tombe. Pour chaque durée de silence de fin de tour : phrase coupée ? délai de fin (ms après la vraie fin) ?"""
    probs = frame_probs(vad, np.clip(np.round(case.render(int((at_ms + agent_stop_ms) * MIC_RATE / 1000))), -32768, 32767)
                        .astype(np.int16).tobytes())
    out = {}
    for es in end_silences:
        events, _ = scan(probs, replace(vcfg, end_silence_ms=es), strict_prob=strict_prob, strict_until_ms=at_ms)
        ends = [e.t_ms for e in events if e.kind == "speech_end" and e.t_ms >= case.user_start_ms]
        late = [t for t in ends if t >= case.user_end_ms]
        out[es] = {"cut": any(t < case.user_end_ms for t in ends), "ended": bool(late),
                   "end_delay_ms": (late[0] - case.user_end_ms) if late else None}
    return out

"""Barge-in : annulation du tour, troncature de l'historique au texte entendu (estimation serveur puis retour du client)."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from src.orchestrator.capture import Capture
from src.orchestrator.history import History
from src.orchestrator.response import ResponseState
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.orchestrator.state import State, StateMachine
from src.orchestrator.tasks import TurnTasks
from src.orchestrator.truncation import heard_text


@dataclass
class _Pending:
    """Instantané d'une interruption en attente de l'accusé du client (`interrupted`)."""
    ref: dict | None          # message de l'agent à affiner (référence stable dans l'historique)
    texts: list[str]
    durs: list[float]
    complete: bool
    t: float                  # monotonic


class Interruptions:
    def __init__(self, deps: SessionDeps, cfg: SessionConfig, resp: ResponseState, history: History, tasks: TurnTasks,
                 sm: StateMachine, capture: Capture) -> None:
        self.deps, self.cfg, self.resp, self.history, self.tasks, self.sm, self.capture = (
            deps, cfg, resp, history, tasks, sm, capture)
        self.carry: bytes | None = None       # audio d'un tour interrompu avant d'être transcrit
        self._pending: deque[_Pending] = deque()   # le client répond une fois par `interrupt`, dans l'ordre

    def take_carry(self) -> bytes | None:
        carry, self.carry = self.carry, None
        return carry

    async def barge_in(self) -> None:
        d, resp = self.deps, self.resp
        t = time.monotonic()
        d.rec("barge_in_detected", voiced_ms=d.endpointer.voiced_ms, state=self.sm.state.value)
        await self.sm.enter(State.BARGE_IN)
        await self.tasks.cancel_all()
        if not resp.user_recorded and resp.current_audio and not resp.ignored:   # le bruit d'un tour ignoré n'est pas repris
            self.carry = resp.current_audio[0]  # la phrase pas encore transcrite sera reprise avec la suivante
        heard, truncated = resp.played_text(resp.played_s(t, self.cfg.playback_margin_s))
        ref = None if resp.ignored else self.history.interrupt(resp.turn_complete, heard, truncated)   # tour ignoré : rien en mémoire
        self._pending.append(_Pending(ref, list(resp.texts), list(resp.durs), resp.turn_complete, t))
        resp.reset()
        self.capture.strict_until = 0.0
        await d.send_json({"type": "interrupt"})
        await d.send_json({"type": "truncate", "text": heard})
        await self.sm.enter(State.LISTENING)  # l'endpointer garde son état : la phrase de l'utilisateur est déjà en cours

    def _next_ack(self) -> _Pending | None:
        """Interruption à laquelle répond ce retour du client ; les plus anciennes restées sans réponse sont écartées."""
        now = time.monotonic()
        while self._pending and now - self._pending[0].t > self.cfg.interrupt_ack_timeout_s:
            self._pending.popleft()
        return self._pending.popleft() if self._pending else None

    async def on_client_ack(self, played_s: float) -> None:
        """Retour du client : durée réellement jouée à l'arrêt. Affine la troncature du message visé, même si un nouveau tour a
        commencé depuis."""
        p = self._next_ack()
        if p is None:
            return
        self.deps.rec("playback_stopped", played_s=round(played_s, 2), ack_ms=round((time.monotonic() - p.t) * 1000, 1))
        heard, truncated = heard_text(p.texts, p.durs, played_s, p.complete)
        self.history.refine(p.ref, heard, truncated)
        await self.deps.send_json({"type": "truncate", "text": heard})

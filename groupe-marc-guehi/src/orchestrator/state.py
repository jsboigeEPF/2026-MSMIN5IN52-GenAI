"""Machine à états de la session : transitions autorisées explicites (voir docs/ARCHITECTURE.md)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from src.metrics.recorder import Recorder


class State(StrEnum):
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    BARGE_IN = "barge_in"      # transitoire (annulation + troncature) ; jamais envoyé au client


ALLOWED: dict[State, frozenset[State]] = {
    State.LISTENING: frozenset({State.THINKING}),
    State.THINKING: frozenset({State.SPEAKING, State.LISTENING, State.BARGE_IN}),
    State.SPEAKING: frozenset({State.LISTENING, State.BARGE_IN}),
    State.BARGE_IN: frozenset({State.LISTENING}),
}


class IllegalTransition(RuntimeError):
    pass


class StateMachine:
    """`to` change l'état (sync) ; `enter` le change et prévient le client (`{"type":"state"}`), sauf pour BARGE_IN.
    Chaque changement émet la trace `state_change` ; une transition illégale lève en mode strict, sinon elle est tracée."""

    def __init__(self, rec: Recorder, send_json: Callable[[dict], Awaitable[None]], strict: bool = False) -> None:
        self.state, self._rec, self._send, self._strict = State.LISTENING, rec, send_json, strict

    @property
    def agent_active(self) -> bool:
        return self.state in (State.THINKING, State.SPEAKING)

    def to(self, new: State) -> bool:
        old = self.state
        if new == old:
            return False
        if new not in ALLOWED[old]:
            if self._strict:
                raise IllegalTransition(f"transition illégale {old} -> {new}")
            self._rec("state_illegal", **{"from": old.value, "to": new.value})
        self.state = new
        self._rec("state_change", **{"from": old.value, "to": new.value})
        return True

    async def enter(self, new: State) -> None:
        if self.to(new) and new is not State.BARGE_IN:
            await self._send({"type": "state", "state": new.value})

    async def ensure_speaking(self) -> None:
        if self.state is not State.SPEAKING:
            await self.enter(State.SPEAKING)

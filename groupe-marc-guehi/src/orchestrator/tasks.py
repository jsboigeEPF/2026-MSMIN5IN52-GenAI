"""Tâches asyncio d'une session : tour, filet de sécurité, STT spéculatif, tâches auxiliaires (annonce, phrase d'attente)."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from src.metrics.recorder import Recorder


class TurnTasks:
    """Possède les tâches ; `cancel_all` les annule et les attend, ce qui évite tâches orphelines et « exception never retrieved »."""

    def __init__(self, rec: Recorder) -> None:
        self._rec = rec
        self.turn: asyncio.Task | None = None
        self.failsafe: asyncio.Task | None = None
        self.spec: asyncio.Task | None = None       # transcription lancée avant la fin de tour
        self.aux: list[asyncio.Task] = []

    def _watch(self, task: asyncio.Task, kind: str, report: bool = True) -> asyncio.Task:
        def done(t: asyncio.Task) -> None:
            if not t.cancelled() and (exc := t.exception()) and report:   # exception lue : jamais « never retrieved »
                self._rec("task_error", kind=kind, error=f"{type(exc).__name__}: {exc}"[:200])
        task.add_done_callback(done)
        return task

    def spawn_turn(self, coro: Coroutine) -> asyncio.Task:
        self.aux = []
        self.turn = self._watch(asyncio.create_task(coro), "turn")
        return self.turn

    def spawn_failsafe(self, coro: Coroutine) -> asyncio.Task:
        self.failsafe = self._watch(asyncio.create_task(coro), "failsafe")
        return self.failsafe

    def spawn_spec(self, coro: Coroutine) -> asyncio.Task:
        """Un échec du STT spéculatif est attendu (repli sur un appel normal) : lu mais non tracé."""
        self.spec = self._watch(asyncio.create_task(coro), "spec", report=False)
        return self.spec

    def spawn_aux(self, coro: Coroutine) -> asyncio.Task:
        task = self._watch(asyncio.create_task(coro), "aux")
        self.aux.append(task)
        return task

    def take_spec(self) -> asyncio.Task | None:
        spec, self.spec = self.spec, None
        return spec

    def cancel_spec(self) -> None:
        if spec := self.take_spec():
            spec.cancel()

    def cancel_failsafe(self) -> None:
        if self.failsafe:
            self.failsafe.cancel()

    def cancel_aux(self) -> None:
        for t in self.aux:
            t.cancel()

    async def cancel_all(self) -> None:
        pending = [t for t in (self.turn, self.failsafe, self.spec, *self.aux) if t]
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.failsafe = self.spec = None
        self.aux = []

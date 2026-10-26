"""Client WebSocket de rejeu : se comporte comme le navigateur (micro simulé, lecture simulée, accusés d'interruption)
et journalise chaque message avec son instant de réception."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Self

import websockets

from src.replay.mic_sim import MicSim


@dataclass
class Ev:
    t: float          # time.monotonic() à la réception
    kind: str         # "audio" ou le champ "type" du message JSON
    data: dict = field(default_factory=dict)


class ReplayClient:
    def __init__(self, url: str, mic: MicSim, sim_playback: bool = False) -> None:
        """sim_playback : True = la lecture dure la durée de l'audio reçu (nécessaire pour tester les interruptions) ;
        False = fin de lecture confirmée aussitôt (mesures de latence)."""
        self.url, self.mic, self.sim_playback = url, mic, sim_playback
        self.events: list[Ev] = []
        self.voice = ""
        self.state = ""
        self.play_start: float | None = None
        self.audio_s = 0.0
        self._cond = asyncio.Condition()
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._ended_task: asyncio.Task | None = None

    async def __aenter__(self) -> Self:
        self.ws = await websockets.connect(self.url, max_size=None)
        self._tasks = [asyncio.create_task(self._recv()), asyncio.create_task(self.mic.run(self.ws.send, self._stop))]
        _, ready = await self.wait_for(lambda e: e.kind == "ready", timeout=20)
        self.voice = ready.data.get("voice", "")
        return self

    async def __aexit__(self, *exc) -> None:
        self._stop.set()
        for t in (*self._tasks, self._ended_task):
            if t:
                t.cancel()
        await asyncio.gather(*[t for t in (*self._tasks, self._ended_task) if t], return_exceptions=True)
        with contextlib.suppress(Exception):
            await self.ws.close()

    def mark(self) -> int:
        return len(self.events)

    async def wait_for(self, pred: Callable[[Ev], bool], after: int = 0, timeout: float = 30.0) -> tuple[int, Ev]:
        """Premier événement (à partir de l'index `after`) vérifiant pred ; TimeoutError sinon."""
        deadline = time.monotonic() + timeout
        async with self._cond:
            while True:
                for i in range(after, len(self.events)):
                    if pred(self.events[i]):
                        return i, self.events[i]
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("événement attendu non reçu")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._cond.wait(), left)

    def since(self, index: int, kind: str | None = None) -> list[Ev]:
        return [e for e in self.events[index:] if kind is None or e.kind == kind]

    async def _log(self, ev: Ev) -> None:
        async with self._cond:
            self.events.append(ev)
            self._cond.notify_all()

    async def _recv(self) -> None:
        try:
            async for msg in self.ws:
                now = time.monotonic()
                if isinstance(msg, bytes):
                    if self.play_start is None:
                        self.play_start = now
                    self.audio_s += len(msg) / 48000
                    self.mic.echo_feed(msg)
                    await self._log(Ev(now, "audio", {"n": len(msg)}))
                    continue
                m = json.loads(msg)
                kind = m.get("type", "")
                if kind == "state":
                    self.state = m["state"]
                elif kind == "user_pending":
                    self.play_start, self.audio_s = None, 0.0
                elif kind == "interrupt":
                    played = now - self.play_start if self.play_start else 0.0
                    self.mic.echo_stop()
                    if self._ended_task:
                        self._ended_task.cancel()
                    self.play_start, self.audio_s = None, 0.0
                    await self.ws.send(json.dumps({"type": "interrupted", "played_s": played}))
                elif kind == "audio_end":
                    self._ended_task = asyncio.create_task(self._playback_done())
                await self._log(Ev(now, kind, m))
        except websockets.ConnectionClosed:
            pass

    async def _playback_done(self) -> None:
        if self.sim_playback and self.play_start is not None:
            await asyncio.sleep(max(0.0, self.play_start + self.audio_s - time.monotonic()))
        self.play_start, self.audio_s = None, 0.0
        with contextlib.suppress(Exception):
            await self.ws.send(json.dumps({"type": "playback_ended"}))

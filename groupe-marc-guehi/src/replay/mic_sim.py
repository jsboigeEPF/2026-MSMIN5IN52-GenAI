"""Micro simulé en temps réel : bruit de fond continu (SNR fixé) + énoncés injectés à la demande, par trames de 32 ms."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

import numpy as np

from src.audio.echo import EchoPath
from src.audio.noise import scale_to_snr, to_f, to_pcm
from src.audio.pcm import SAMPLE_RATE_OUT, resample

FRAME = 512  # échantillons à 16 kHz = 32 ms
FRAME_S = FRAME / 16000


class MicSim:
    def __init__(self, noise: np.ndarray | None = None, ref_rms: float = 3000.0, snr_db: float | None = None,
                 rng: np.random.Generator | None = None, echo: EchoPath | None = None) -> None:
        """noise : signal de bruit (boucle) ; son niveau est fixé une fois pour toutes à ref_rms / SNR.
        echo : si fourni, l'audio de l'agent (`echo_feed`) est réinjecté dans le micro (haut-parleur sans casque)."""
        self.echo, self._pos = echo, 0          # _pos : horloge d'échantillons du micro
        self.noise_on = True  # permet d'allumer le bruit en cours d'essai (ex. pendant que l'agent parle)
        self._noise = scale_to_snr(noise, ref_rms, snr_db) if noise is not None and snr_db is not None else None
        self._npos = int((rng or np.random.default_rng(0)).integers(len(self._noise))) if self._noise is not None else 0
        self._queue: deque[tuple[np.ndarray, asyncio.Future]] = deque()
        self._cur: np.ndarray | None = None
        self._fut: asyncio.Future | None = None
        self._cpos = 0
        self._t_start = 0.0

    def play(self, samples: np.ndarray) -> asyncio.Future:
        """Injecte un énoncé (float, 16 kHz). Le futur renvoie (t_début, t_fin) : instants monotones d'envoi de la
        première et de la dernière trame contenant de la parole."""
        fut = asyncio.get_running_loop().create_future()
        self._queue.append((np.asarray(samples, dtype=float), fut))
        return fut

    def echo_feed(self, pcm24: bytes) -> None:
        """Audio de l'agent (PCM16 24 kHz) reçu par le client simulé : il sera joué puis recapté avec le retard de l'écho."""
        if self.echo is not None:
            self.echo.feed(to_f(resample(pcm24, SAMPLE_RATE_OUT, 16000)), self._pos)

    def echo_stop(self) -> None:
        """Lecture interrompue côté client : le haut-parleur se tait."""
        if self.echo is not None:
            self.echo.stop(self._pos)

    def _next_frame(self) -> tuple[np.ndarray, bool, bool]:
        f = np.zeros(FRAME)
        if self._noise is not None:
            if self.noise_on:
                f += self._noise[(self._npos + np.arange(FRAME)) % len(self._noise)]
            self._npos = (self._npos + FRAME) % len(self._noise)
        if self.echo is not None:
            f += self.echo.read(self._pos, FRAME)
        self._pos += FRAME
        started = ended = False
        if self._cur is None and self._queue:
            self._cur, self._fut = self._queue.popleft()
            self._cpos, started = 0, True
        if self._cur is not None:
            seg = self._cur[self._cpos:self._cpos + FRAME]
            f[:len(seg)] += seg
            self._cpos += len(seg)
            if self._cpos >= len(self._cur):
                ended = True
        return f, started, ended

    async def run(self, send: Callable[[bytes], Awaitable[None]], stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        t0, k = loop.time(), 0
        while not stop.is_set():
            f, started, ended = self._next_frame()
            await send(to_pcm(f))
            now = time.monotonic()
            if started:
                self._t_start = now
            if ended and self._fut is not None:
                if not self._fut.done():
                    self._fut.set_result((self._t_start, now))
                self._cur = self._fut = None
            k += 1
            await asyncio.sleep(max(0.0, t0 + k * FRAME_S - loop.time()))

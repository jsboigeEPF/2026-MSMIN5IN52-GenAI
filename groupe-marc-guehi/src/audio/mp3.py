"""Décodage mp3 -> PCM16 mono en flux (MiniMax ne streame qu'en mp3). Utilise miniaudio, sans ffmpeg."""
from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator

import miniaudio


class _QueueSource(miniaudio.StreamableSource):
    """Source lue par le décodeur dans son thread ; bloque jusqu'à l'arrivée d'octets ou de la fin."""

    def __init__(self) -> None:
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._buf = b""
        self._eof = False

    def feed(self, data: bytes | None) -> None:
        self._q.put(data)

    def read(self, num_bytes: int) -> bytes:
        if not self._buf and not self._eof:
            item = self._q.get()
            if item is None:
                self._eof = True
            else:
                self._buf = item
        out, self._buf = self._buf[:num_bytes], self._buf[num_bytes:]
        return out


class Mp3StreamDecoder:
    """feed() les octets mp3 au fil de l'eau, finish() à la fin ; itérer pour obtenir des chunks PCM16."""

    def __init__(self, sample_rate: int = 24000, frames_per_chunk: int = 1200) -> None:
        self._rate, self._frames = sample_rate, frames_per_chunk
        self._src = _QueueSource()
        self._loop = asyncio.get_running_loop()
        self._out: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()
        self._finished = False
        threading.Thread(target=self._run, daemon=True).start()

    def feed(self, data: bytes) -> None:
        if not self._finished:
            self._src.feed(data)

    def finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._src.feed(None)

    def _put(self, item) -> None:
        try:
            self._loop.call_soon_threadsafe(self._out.put_nowait, item)
        except RuntimeError:  # boucle déjà fermée (annulation)
            pass

    def _run(self) -> None:
        try:
            gen = miniaudio.stream_any(self._src, miniaudio.FileFormat.MP3, miniaudio.SampleFormat.SIGNED16,
                                       nchannels=1, sample_rate=self._rate, frames_to_read=self._frames)
            for chunk in gen:
                self._put(chunk.tobytes())
        except Exception as e:  # noqa: BLE001  flux mp3 invalide ou tronqué : transmis à l'appelant
            self._put(e)
        finally:
            self._put(None)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._out.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

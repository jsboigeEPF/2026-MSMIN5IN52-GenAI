"""PortAudio callback only copies into a bounded thread-safe queue."""

import asyncio
import queue
import time

from voiceagent.config import AudioConfig
from voiceagent.domain import Frame


class Microphone:
    def __init__(self, config: AudioConfig, events):
        self.config = config
        self.events = events
        self._queue: queue.Queue[Frame] = queue.Queue(config.capture_queue)
        self._stream = None
        self._dropped = 0
        self._status = ""

    async def __aenter__(self):
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            blocksize=512,
            channels=1,
            dtype="float32",
            device=self.config.input_device,
            callback=self._callback,
        )
        await asyncio.to_thread(self._stream.start)
        return self

    def _callback(self, data, frames, timing, status):
        if status:
            self._status = str(status)
        offset_s = timing.inputBufferAdcTime + frames / 16000 - timing.currentTime
        end_ns = time.perf_counter_ns() + int(offset_s * 1e9)
        frame = Frame(data[:, 0].copy(), end_ns)
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            self._dropped += 1

    async def frames(self):
        count = 0
        since = time.perf_counter()
        while True:
            try:
                frame = self._queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.002)
                continue
            count += 1
            now = time.perf_counter()
            if now - since >= 1:
                self.events.emit(
                    "capture_stats",
                    blocks_per_second=count / (now - since),
                    dropped=self._dropped,
                    status=self._status,
                )
                count = 0
                since = now
            yield frame

    async def __aexit__(self, *args):
        if self._stream:
            await asyncio.to_thread(self._stream.close)

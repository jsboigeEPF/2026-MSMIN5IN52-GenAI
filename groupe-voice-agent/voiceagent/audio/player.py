"""Interruptible output with DAC-clock estimates, not enqueue timestamps."""

import asyncio
import time
from dataclasses import dataclass

import numpy as np

from voiceagent.audio.reference import PlaybackReference
from voiceagent.audio.wav import resample
from voiceagent.config import AudioConfig
from voiceagent.domain import AudioChunk, Samples
from voiceagent.vad import rms


@dataclass
class Pending:
    samples: Samples
    turn: int
    text: str
    future: asyncio.Future
    generation: int
    position: int = 0
    started: bool = False


class SoundPlayer:
    audible = True

    def __init__(self, config: AudioConfig, events):
        self.config = config
        self.events = events
        self._loop = asyncio.get_running_loop()
        self._stream = None
        self._pending: Pending | None = None
        self._generation = 0
        self._first_turn = -1
        self._reference_rms = 0.0
        self._reference_time = 0.0
        self.reference = PlaybackReference()

    @property
    def reference_rms(self) -> float:
        if time.perf_counter() - self._reference_time > 0.25:
            return 0.0
        return self._reference_rms

    async def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.OutputStream(
            samplerate=self.config.output_rate,
            blocksize=self.config.output_rate * self.config.output_block_ms // 1000,
            channels=1,
            dtype="float32",
            device=self.config.output_device,
            latency="low",
            callback=self._callback,
        )
        await asyncio.to_thread(self._stream.start)

    async def play(self, chunk: AudioChunk, turn: int) -> bool:
        samples = await asyncio.to_thread(
            resample, chunk.samples, chunk.sample_rate, self.config.output_rate
        )
        if not len(samples) or not np.isfinite(samples).all():
            raise ValueError("Audio TTS vide ou non fini")
        future = self._loop.create_future()
        self._pending = Pending(
            np.clip(samples, -1, 1), turn, chunk.text, future, self._generation
        )
        try:
            return await future
        finally:
            if self._pending and self._pending.future is future:
                self._pending = None

    def _callback(self, output, frames, timing, status):
        output.fill(0)
        pending = self._pending
        if pending is None or pending.generation != self._generation:
            return
        remaining = len(pending.samples) - pending.position
        count = min(frames, remaining)
        if count <= 0:
            return
        output[:count, 0] = pending.samples[pending.position : pending.position + count]
        self._reference_rms = rms(output[:, 0])
        self._reference_time = time.perf_counter()
        now_ns = time.perf_counter_ns()
        dac_ns = now_ns + int((timing.outputBufferDacTime - timing.currentTime) * 1e9)
        if not pending.started:
            pending.started = True
            self._loop.call_soon_threadsafe(self._started, pending, dac_ns)
        pending.position += count
        if pending.position == len(pending.samples):
            end_ns = dac_ns + int(count / self.config.output_rate * 1e9)
            self._loop.call_soon_threadsafe(self._schedule_complete, pending, end_ns)
        if status:
            self._loop.call_soon_threadsafe(self._status, str(status), pending.turn)

    def _status(self, status: str, turn: int) -> None:
        self.events.emit("audio_status", turn, status=status)

    def _started(self, pending: Pending, t_ns: int) -> None:
        if pending.generation != self._generation:
            return
        self.reference.add(t_ns, pending.samples, self.config.output_rate)
        if pending.turn != self._first_turn:
            self._first_turn = pending.turn
            self.events.emit("playback_start", pending.turn, t_ns=t_ns, clock="portaudio_dac")
        self.events.emit("playback_chunk_start", pending.turn, t_ns=t_ns, text=pending.text)

    def _schedule_complete(self, pending: Pending, end_ns: int) -> None:
        delay = max(0, (end_ns - time.perf_counter_ns()) / 1e9)
        self._loop.call_later(delay, self._complete, pending, end_ns)

    def _complete(self, pending: Pending, end_ns: int) -> None:
        if pending.generation != self._generation or pending.future.done():
            return
        self.events.emit("played_chunk", pending.turn, t_ns=end_ns, text=pending.text)
        pending.future.set_result(True)

    async def stop(self) -> int:
        # Invalidate first: no delayed callback can acknowledge discarded audio.
        self._generation += 1
        pending = self._pending
        self._pending = None
        if pending and not pending.future.done():
            pending.future.set_result(False)
        if self._stream:
            await asyncio.to_thread(self._stream.abort)
        stopped_ns = time.perf_counter_ns()
        self.reference.stop(stopped_ns)
        if self._stream:
            await asyncio.to_thread(self._stream.start)
        return stopped_ns

    async def close(self) -> None:
        self._generation += 1
        if self._stream:
            await asyncio.to_thread(self._stream.abort)
            await asyncio.to_thread(self._stream.close)
            self._stream = None


class SimulatedPlayer:
    audible = True
    reference_rms = 0.0

    def __init__(self, events):
        self.events = events
        self._generation = 0
        self._first_turn = -1
        self.reference = PlaybackReference()

    async def play(self, chunk: AudioChunk, turn: int) -> bool:
        generation = self._generation
        self.reference.add(time.perf_counter_ns(), chunk.samples, chunk.sample_rate)
        if turn != self._first_turn:
            self._first_turn = turn
            self.events.emit("playback_start", turn, clock="simulated")
        self.events.emit("playback_chunk_start", turn, text=chunk.text)
        await asyncio.sleep(len(chunk.samples) / chunk.sample_rate)
        if generation != self._generation:
            return False
        self.events.emit("played_chunk", turn, text=chunk.text)
        return True

    async def stop(self) -> int:
        self._generation += 1
        stopped = time.perf_counter_ns()
        self.reference.stop(stopped)
        return stopped

    async def close(self) -> None:
        await self.stop()


class TextPlayer:
    audible = False
    reference_rms = 0.0

    async def play(self, chunk: AudioChunk, turn: int) -> bool:
        return True

    async def stop(self) -> int:
        return time.perf_counter_ns()

    async def close(self) -> None:
        return None

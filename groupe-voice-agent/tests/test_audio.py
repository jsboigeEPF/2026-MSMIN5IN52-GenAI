import asyncio
import time
import unittest
from types import SimpleNamespace

import numpy as np

from tests.helpers import MemoryEvents
from voiceagent.audio.player import SoundPlayer
from voiceagent.audio.reference import PlaybackReference
from voiceagent.config import AudioConfig
from voiceagent.domain import AudioChunk


class FakeStream:
    def __init__(self):
        self.aborted = False

    def abort(self):
        self.aborted = True

    def start(self):
        return None

    def close(self):
        return None


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = MemoryEvents()
        self.player = SoundPlayer(AudioConfig(), self.events)
        self.player._stream = FakeStream()

    async def asyncTearDown(self):
        await self.player.close()

    async def test_dac_ack_waits_until_samples_have_played(self):
        chunk = AudioChunk("Bonjour", np.ones(480, dtype=np.float32), 24000)
        task = asyncio.create_task(self.player.play(chunk, 1))
        await asyncio.sleep(0.02)
        output = np.zeros((480, 1), dtype=np.float32)
        timing = SimpleNamespace(currentTime=2.0, outputBufferDacTime=2.05)
        self.player._callback(output, 480, timing, None)
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        self.assertTrue(np.all(output == 1))
        self.assertTrue(await task)
        self.assertEqual(len(self.events.named("played_chunk")), 1)

    async def test_abort_invalidates_delayed_completion(self):
        chunk = AudioChunk("Bonjour", np.ones(480, dtype=np.float32), 24000)
        task = asyncio.create_task(self.player.play(chunk, 1))
        await asyncio.sleep(0.02)
        timing = SimpleNamespace(currentTime=2.0, outputBufferDacTime=2.03)
        self.player._callback(np.empty((480, 1), dtype=np.float32), 480, timing, None)
        await self.player.stop()
        self.assertFalse(await task)
        await asyncio.sleep(0.08)
        self.assertEqual(self.events.named("played_chunk"), [])
        self.assertTrue(self.player._stream.aborted)

    async def test_callback_silences_output_after_stop(self):
        await self.player.stop()
        output = np.ones((480, 1), dtype=np.float32)
        self.player._callback(output, 480, None, None)
        self.assertTrue(np.all(output == 0))

    async def test_empty_audio_is_rejected(self):
        with self.assertRaises(ValueError):
            await self.player.play(AudioChunk("", np.zeros(0, dtype=np.float32), 24000), 1)


class EchoTests(unittest.TestCase):
    def test_delayed_attenuated_reference_and_abort_cutoff(self):
        reference = PlaybackReference()
        origin = time.perf_counter_ns()
        reference.add(origin, np.ones(24000, dtype=np.float32), 24000)
        frame = reference.sample(origin + 100_000_000)
        np.testing.assert_allclose(frame, 10 ** (-10 / 20))
        reference.stop(origin + 100_000_000)
        frame = reference.sample(origin + 200_000_000)
        self.assertTrue(np.all(frame == 0))

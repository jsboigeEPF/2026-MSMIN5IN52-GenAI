import asyncio
import time
import unittest

import numpy as np

from tests.helpers import MemoryEvents
from voiceagent.agent import Agent
from voiceagent.audio.player import SimulatedPlayer, TextPlayer
from voiceagent.backends.mock import MockLlm, MockStt, MockTts
from voiceagent.config import Config
from voiceagent.domain import AudioChunk, State
from voiceagent.history import History
from voiceagent.workers import SerialWorker


class LongTts:
    def synthesize(self, text):
        return AudioChunk(text, np.zeros(48000, dtype=np.float32), 24000)


class SlowLlm:
    closed = False

    async def stream(self, messages):
        try:
            await asyncio.sleep(10)
            yield "Trop tard. "
        finally:
            self.closed = True


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = MemoryEvents()
        self.agent = Agent(
            Config(), self.events, MockStt(), MockLlm(), MockTts(), SimulatedPlayer(self.events)
        )

    async def asyncTearDown(self):
        await self.agent.close()

    async def test_complete_response_commits_played_text(self):
        await self.agent.submit_text("Bonjour")
        await self.agent.wait_idle()
        commit = self.events.named("history_commit")[0]["data"]
        self.assertIn("VAD", commit["assistant"])
        self.assertFalse(commit["interrupted"])
        self.assertEqual(self.agent.state, State.LISTENING)

    async def test_playback_starts_before_llm_finishes(self):
        await self.agent.submit_text("Bonjour")
        await self.agent.wait_idle()
        start = self.events.named("playback_start")[0]["t_ns"]
        done = self.events.named("llm_done")[0]["t_ns"]
        self.assertLess(start, done)

    async def test_interrupt_during_generation_closes_stream(self):
        model = SlowLlm()
        self.agent.llm = model
        await self.agent.submit_text("Question")
        await asyncio.sleep(0.01)
        await self.agent.interrupt()
        self.assertTrue(model.closed)
        self.assertEqual(
            self.events.named("history_commit")[0]["data"]["assistant"], "[interrompu]"
        )
        self.assertFalse(self.agent.busy)

    async def test_interrupt_during_playback_does_not_commit_partial_sentence(self):
        self.agent.tts = LongTts()
        await self.agent.submit_text("Question")
        async with asyncio.timeout(2):
            while not self.events.named("playback_start"):  # noqa: ASYNC110
                await asyncio.sleep(0.005)
        await self.agent.interrupt()
        commit = self.events.named("history_commit")[0]["data"]
        self.assertEqual(commit["assistant"], "[interrompu]")
        await asyncio.sleep(0.02)
        self.assertEqual(self.events.named("played_chunk"), [])

    async def test_pipeline_error_returns_to_listening_and_next_turn_works(self):
        class FailingTts:
            def synthesize(self, text):
                raise RuntimeError("intentional failure")

        self.agent.tts = FailingTts()
        await self.agent.submit_text("Question")
        await self.agent.wait_idle()
        self.assertTrue(self.events.named("error"))
        self.assertEqual(self.events.named("error")[0]["data"]["stage"], "tts")
        self.assertEqual(self.agent.state, State.LISTENING)
        self.agent.tts = MockTts()
        await self.agent.submit_text("Autre question")
        await self.agent.wait_idle()
        self.assertTrue(self.events.named("turn_done"))

    async def test_timeout_does_not_leave_background_response(self):
        self.agent.llm = SlowLlm()
        self.agent.config.turn_timeout_s = 0.03
        await self.agent.submit_text("Question")
        await self.agent.wait_idle()
        self.assertFalse(self.agent.busy)
        self.assertTrue(self.events.named("error"))

    async def test_failed_output_abort_still_cancels_generation(self):
        class BrokenPlayer(TextPlayer):
            async def stop(self):
                raise OSError("device disconnected")

        model = SlowLlm()
        self.agent.llm = model
        self.agent.player = BrokenPlayer()
        await self.agent.submit_text("Question")
        await asyncio.sleep(0.01)
        delay = await self.agent.interrupt()
        self.assertIsNone(delay)
        self.assertTrue(model.closed)
        self.assertFalse(self.agent.busy)
        self.assertEqual(self.events.named("playback_stopped"), [])

    async def test_text_only_does_not_emit_audio_measurements(self):
        self.agent.player = TextPlayer()
        self.agent.tts = None
        await self.agent.submit_text("Bonjour")
        await self.agent.wait_idle()
        self.assertFalse(self.events.named("playback_start"))
        self.assertFalse(self.events.named("tts_first_chunk"))

    async def test_second_question_cancels_first(self):
        self.agent.tts = LongTts()
        await self.agent.submit_text("Première")
        await asyncio.sleep(0.03)
        self.agent.tts = MockTts()
        await self.agent.submit_text("Deuxième")
        await self.agent.wait_idle()
        commits = self.events.named("history_commit")
        self.assertEqual([item["data"]["user"] for item in commits], ["Première", "Deuxième"])

    async def test_native_worker_never_overlaps_after_cancellation(self):
        worker = SerialWorker("test")
        calls = []

        def calculate(name):
            calls.append((name, "start"))
            time.sleep(0.04)
            calls.append((name, "end"))

        first = asyncio.create_task(worker.run(calculate, "first"))
        await asyncio.sleep(0.01)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await worker.run(calculate, "second")
        worker.close()
        self.assertEqual(
            calls,
            [("first", "start"), ("first", "end"), ("second", "start"), ("second", "end")],
        )


class HistoryTests(unittest.TestCase):
    def test_keeps_six_pairs_not_six_messages(self):
        history = History("system", 6)
        for index in range(10):
            history.commit(str(index), [f"réponse {index}"])
        messages = history.messages("next")
        self.assertEqual(len(messages), 14)
        self.assertEqual(messages[1]["content"], "4")

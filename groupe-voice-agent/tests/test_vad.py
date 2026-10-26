import unittest

import numpy as np

from voiceagent.config import VadConfig
from voiceagent.domain import Frame
from voiceagent.vad import TurnDetector


class VadTests(unittest.TestCase):
    def setUp(self):
        self.detector = TurnDetector(VadConfig())
        self.index = 0

    def feed(self, probability, **kwargs):
        self.index += 1
        frame = Frame(np.full(512, 0.2, dtype=np.float32), self.index * 32_000_000)
        return self.detector.feed(frame, probability, **kwargs)

    def test_start_requires_two_frames(self):
        self.assertEqual(self.feed(0.9), [])
        self.assertEqual(self.feed(0.9)[0].kind, "speech_start")

    def test_endpoint_rounds_500ms_to_512ms(self):
        for _ in range(12):
            self.feed(0.9)
        for _ in range(15):
            self.assertEqual(self.feed(0.0), [])
        utterance = self.feed(0.0)[0].utterance
        self.assertEqual(utterance.end_ns, 12 * 32_000_000)
        self.assertEqual(utterance.endpoint_ns - utterance.end_ns, 512_000_000)
        self.assertEqual(len(utterance.samples), 12 * 512)

    def test_barge_requires_ten_frames_and_preserves_them(self):
        for _ in range(9):
            self.assertEqual(self.feed(0.9, busy=True), [])
        self.assertEqual(self.feed(0.9, busy=True)[0].kind, "barge_start")
        for _ in range(15):
            self.feed(0.0)
        utterance = self.feed(0.0)[0].utterance
        self.assertEqual(len(utterance.samples), 10 * 512)
        self.assertEqual(utterance.start_ns, 0)

    def test_cough_does_not_interrupt(self):
        for _ in range(8):
            self.assertEqual(self.feed(0.99, busy=True), [])
        self.assertEqual(self.feed(0, busy=True), [])
        for _ in range(9):
            self.assertEqual(self.feed(0.99, busy=True), [])

    def test_energy_gate_blocks_echo(self):
        for _ in range(15):
            self.assertEqual(self.feed(0.95, busy=True, reference_rms=1.0), [])

    def test_half_duplex_ignores_speech_while_busy(self):
        for _ in range(30):
            self.assertEqual(self.feed(0.99, busy=True, half_duplex=True), [])

    def test_short_speech_is_discarded(self):
        for _ in range(2):
            self.feed(0.9)
        for _ in range(15):
            self.feed(0)
        self.assertEqual(self.feed(0)[0].kind, "discarded")

    def test_preroll_includes_up_to_seven_frames(self):
        for _ in range(20):
            self.feed(0)
        for _ in range(10):
            self.feed(0.9)
        for _ in range(15):
            self.feed(0)
        utterance = self.feed(0)[0].utterance
        self.assertEqual(len(utterance.samples), (7 + 10) * 512)

    def test_maximum_turn_duration_is_bounded(self):
        self.detector.config.max_speech_s = 1
        result = []
        for _ in range(32):
            result.extend(self.feed(0.9))
        self.assertEqual(result[-1].kind, "endpoint")

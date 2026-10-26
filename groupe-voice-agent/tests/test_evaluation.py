import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.helpers import MemoryEvents
from voiceagent.audio.wav import read_wav, write_wav
from voiceagent.evaluation.metrics import read_events, summarize, turn_rows
from voiceagent.evaluation.noise import generate_noise, mix_noise
from voiceagent.evaluation.statistics import describe, normalize, wilson, word_errors
from voiceagent.events import EventBus
from voiceagent.vad import rms


class EvaluationTests(unittest.TestCase):
    def test_exact_snr_without_clipping(self):
        rng = np.random.default_rng(42)
        clean = rng.normal(0, 0.3, 32000).astype(np.float32)
        noise = generate_noise("white", len(clean), rng)
        mixed, info = mix_noise(clean, noise, 5)
        gain = info["gain"]
        measured = 20 * np.log10(rms(clean * gain) / rms(mixed - clean * gain))
        self.assertAlmostEqual(measured, 5, places=4)
        self.assertLessEqual(np.max(np.abs(mixed)), 0.981)

    def test_pink_noise_is_seeded(self):
        left = generate_noise("pink", 1024, np.random.default_rng(7))
        right = generate_noise("pink", 1024, np.random.default_rng(7))
        np.testing.assert_array_equal(left, right)

    def test_zero_signal_rejects_snr(self):
        with self.assertRaises(ValueError):
            mix_noise(np.zeros(5), np.ones(5), 10)

    def test_clean_is_unchanged(self):
        samples = np.ones(5, dtype=np.float32)
        mixed, _ = mix_noise(samples, samples, None)
        np.testing.assert_array_equal(samples, mixed)

    def test_percentiles_and_bootstrap(self):
        result = describe([10, 20, 30, 40], seed=4)
        self.assertEqual(result["p50"], 25)
        self.assertAlmostEqual(result["p95"], 38.5)
        self.assertFalse(result["p95_reliable"])
        self.assertEqual(result, describe([10, 20, 30, 40], seed=4))

    def test_empty_statistics(self):
        self.assertEqual(describe([]), {"n": 0})
        self.assertIsNone(wilson(0, 0)["rate"])

    def test_wilson_does_not_claim_certainty_for_one_success(self):
        result = wilson(1, 1)
        self.assertLess(result["ci_low"], 0.3)
        self.assertAlmostEqual(result["ci_high"], 1)

    def test_french_wer_normalizes_punctuation(self):
        self.assertEqual(normalize("L'été, c’est beau !"), "l été c est beau")
        self.assertEqual(word_errors("Bonjour, le monde !", "bonjour le monde")["wer"], 0)
        self.assertEqual(word_errors("un deux trois", "un trois")["errors"], 1)

    def test_missing_transcription_counts_as_deletions(self):
        events = MemoryEvents()
        events.emit("eval_reference", 1, reference="un deux trois", noise="pink", snr_db=10)
        row = turn_rows(events.items)[0]
        self.assertEqual(row["wer"], 1)

    def test_sessions_and_modes_are_separate(self):
        events = MemoryEvents()
        events.emit("speech_end_real", 1, t_ns=1_000_000)
        events.emit("playback_start", 1, t_ns=101_000_000)
        other = [dict(item, session="second", mode="live") for item in events.items]
        rows = turn_rows(events.items + other)
        self.assertEqual(len(rows), 2)
        summary = summarize(rows, events.items + other)
        self.assertEqual(set(summary["groups"]), {"simulated/local", "live/local"})

    def test_missing_audio_does_not_become_zero_latency(self):
        events = MemoryEvents()
        events.emit("speech_end_real", 1)
        self.assertNotIn("response_ms", turn_rows(events.items)[0])

    def test_jsonl_is_flushed_and_parseable(self):
        with tempfile.TemporaryDirectory() as root:
            bus = EventBus(Path(root), "simulated", "test")
            bus.emit("example", 1, text="été")
            bus.close()
            self.assertEqual(len(read_events(bus.directory / "events.jsonl")), 3)

    def test_broken_jsonl_identifies_line(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "events.jsonl"
            path.write_text(json.dumps({"turn": 1}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, ":1:"):
                read_events(path)

    def test_wav_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "sample.wav"
            samples = np.sin(np.arange(1600) / 20).astype(np.float32) * 0.25
            write_wav(path, samples)
            np.testing.assert_allclose(read_wav(path), samples, atol=4e-5)

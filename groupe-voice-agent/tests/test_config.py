import tempfile
import unittest
from pathlib import Path

from voiceagent.config import Config, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        Config().validate()

    def test_unsupported_sample_rate_is_rejected(self):
        config = Config()
        config.audio.sample_rate = 44100
        with self.assertRaises(ValueError):
            config.validate()

    def test_unknown_setting_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.yaml"
            path.write_text("tts:\n  enigne: kokoro\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

import random

import pytest

from src.tts.voices import pick_voice, voices_from_env


def test_pick_voice_returns_a_configured_voice():
    voices = ["French_Male_Speech_New", "French_Female_Speech_New"]
    assert pick_voice(voices, random.Random(0)) in voices


def test_both_voices_can_be_drawn():
    voices = ["a", "b"]
    drawn = {pick_voice(voices, random.Random(seed)) for seed in range(20)}
    assert drawn == {"a", "b"}


def test_empty_list_raises():
    with pytest.raises(ValueError):
        pick_voice([])


def test_voices_from_env(monkeypatch):
    monkeypatch.setenv("TTS_VOICES", " x , y ,")
    assert voices_from_env() == ["x", "y"]

import numpy as np

from src.audio.noise import babble, mix, rms, scale_to_snr, speech_rms, white


def test_scale_to_snr_gives_requested_ratio():
    rng = np.random.default_rng(0)
    speech = 3000 * np.sin(np.arange(16000) * 0.1)
    for snr in (20, 10, 0, -5):
        noise = scale_to_snr(white(16000, rng), rms(speech), snr)
        assert abs(20 * np.log10(rms(speech) / rms(noise)) - snr) < 0.01


def test_mix_covers_silence_and_keeps_length():
    rng = np.random.default_rng(1)
    speech = np.concatenate([np.zeros(8000), 4000 * np.sin(np.arange(8000) * 0.2)])
    out = mix(speech, white(100, rng), speech_rms(speech), 10)
    assert len(out) == len(speech) and rms(out[:8000]) > 0


def test_speech_rms_ignores_silence():
    tone = 4000 * np.sin(np.arange(16000) * 0.2)
    padded = np.concatenate([np.zeros(48000), tone])
    assert abs(speech_rms(padded) - rms(tone)) / rms(tone) < 0.02


def test_babble_is_deterministic_and_has_requested_length():
    clips = [np.random.default_rng(i).standard_normal(16000 * 2) for i in range(3)]
    a = babble(clips, 40000, np.random.default_rng(5))
    b = babble(clips, 40000, np.random.default_rng(5))
    assert len(a) == 40000 and np.array_equal(a, b)

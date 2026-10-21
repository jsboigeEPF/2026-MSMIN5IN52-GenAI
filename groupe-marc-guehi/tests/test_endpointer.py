from src.vad.endpointer import Endpointer, VadConfig

FRAME = b"\x01\x00" * 512


def run(probs: list[float], cfg: VadConfig | None = None):
    ep, events = Endpointer(cfg), []
    for p in probs:
        events += ep.push(FRAME, p)
    return events


def test_speech_then_silence_gives_start_and_end():
    ev = run([0.0] * 5 + [0.9] * 20 + [0.0] * 20)
    assert [e.kind for e in ev] == ["speech_start", "speech_end"]
    assert ev[1].speech_ms >= 20 * 32 - 64


def test_end_needs_enough_silence():
    cfg = VadConfig(end_silence_ms=450)
    ev = run([0.9] * 10 + [0.0] * 10, cfg)  # 10 trames = 320 ms < 450 ms
    assert [e.kind for e in ev] == ["speech_start"]


def test_short_noise_burst_is_ignored():
    assert run([0.9, 0.9] + [0.0] * 30) == []  # 64 ms < min_speech_ms


def test_brief_voiced_segment_is_discarded():
    cfg = VadConfig(min_speech_ms=96, min_voiced_ms=300)
    ev = run([0.9] * 4 + [0.0] * 20, cfg)  # 128 ms de voix < 300 ms
    assert [e.kind for e in ev] == ["speech_start", "discarded"]


def test_preroll_is_included_and_state_resets():
    cfg = VadConfig(preroll_ms=96)
    ev = run([0.0] * 10 + [0.9] * 10 + [0.0] * 20, cfg)
    end = next(e for e in ev if e.kind == "speech_end")
    assert len(end.pcm) // 1024 >= 10 + 3  # parole + pré-roll
    assert [e.kind for e in run([0.9] * 10 + [0.0] * 20 + [0.9] * 10 + [0.0] * 20)].count("speech_end") == 2


def test_hysteresis_keeps_speech_alive_between_thresholds():
    ev = run([0.9] * 10 + [0.4] * 30)  # 0.4 > neg_threshold : pas du silence
    assert [e.kind for e in ev] == ["speech_start"]

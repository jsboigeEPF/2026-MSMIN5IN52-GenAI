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


def test_level_gate_ignores_quiet_frames():
    import numpy as np

    from src.audio.pcm import rms_dbfs
    from src.orchestrator.capture import Capture
    quiet = (np.ones(512) * 30).astype(np.int16).tobytes()      # ≈ -60 dBFS
    loud = (np.ones(512) * 3000).astype(np.int16).tobytes()     # ≈ -21 dBFS
    assert rms_dbfs(quiet) < -50 < rms_dbfs(loud) and rms_dbfs(b"") == -120.0

    class Vad:
        def prob(self, f): return 0.9
        def reset(self): pass

    class Deps:
        vad, endpointer = Vad(), Endpointer(VadConfig(min_level_dbfs=-50))

    cap = Capture.__new__(Capture)
    cap.deps, cap._buf, cap.strict_until = Deps, b"", 0.0
    cap.cfg = type("C", (), {"barge_prob": 0.6, "speculative_ms": 0, "barge_min_ms": 300})()
    cap._agent_active = lambda: False
    assert [s.kind for s in cap.frames(quiet * 10)] == []
    assert "speech_start" in [s.kind for s in cap.frames(loud * 10)]

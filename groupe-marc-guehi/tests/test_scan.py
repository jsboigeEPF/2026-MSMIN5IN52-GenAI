from src.vad.endpointer import VadConfig
from src.vad.scan import scan


def test_scan_reports_times_and_barge():
    probs = [0.0] * 10 + [0.95] * 30 + [0.0] * 20
    events, barge = scan(probs, VadConfig(end_silence_ms=450), strict_prob=0.75, barge_ms=300)
    kinds = [e.kind for e in events]
    assert kinds == ["speech_start", "speech_end"]
    assert events[0].t_ms == (10 + 3) * 32           # 96 ms de parole continue
    assert barge is not None and barge >= events[0].t_ms + 200


def test_weak_voice_never_triggers_under_strict_gate():
    events, barge = scan([0.6] * 100, VadConfig(), strict_prob=0.75, barge_ms=300)
    assert events == [] and barge is None

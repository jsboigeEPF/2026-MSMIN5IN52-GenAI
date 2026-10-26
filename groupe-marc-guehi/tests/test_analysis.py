import math

from src.metrics.analysis import barge_verdict, is_prefix_of_sent, normalize_fr, percentiles, wer


def test_normalize_and_wer():
    assert normalize_fr("Bonjour, c’est l'été !") == "bonjour c est l été"
    assert wer("Bonjour, tu m'entends bien ?", "bonjour tu m entends bien") == 0.0
    assert wer("un deux trois quatre", "un deux trois") == 0.25
    assert wer("un deux", "") == 1.0


def test_percentiles_ignore_missing_values():
    p = percentiles([100, 200, None, 300, float("nan"), 400])
    assert p["n"] == 4 and p["p50"] == 250
    assert math.isnan(percentiles([])["p50"])


def test_truncation_must_be_prefix_of_what_was_sent():
    sent = ["Alors, voyons.", "La latence, c'est le délai.", "Il est court."]
    assert is_prefix_of_sent("Alors, voyons. La latence, c'est le…", sent)
    assert not is_prefix_of_sent("Alors, voyons. Il est court.", sent)


def test_barge_verdict_requires_all_criteria():
    good = {"detect_ms": 600, "frames_after_interrupt": 0, "truncate_text": "Alors,", "truncate_prefix_ok": True,
            "wer": 0.1, "followup_audio": True}
    assert barge_verdict(good, 800, 0.25)["ok"]
    for key, bad in [("detect_ms", 900), ("frames_after_interrupt", 3), ("truncate_prefix_ok", False), ("wer", 0.5),
                     ("followup_audio", False)]:
        v = barge_verdict({**good, key: bad}, 800, 0.25)
        assert not v["ok"], key
    assert not barge_verdict({"detect_ms": None}, 800, 0.25)["ok"]

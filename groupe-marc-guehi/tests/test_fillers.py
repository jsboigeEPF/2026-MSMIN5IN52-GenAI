import random
from itertools import pairwise

import numpy as np

from src.tts.fillers import Filler, FillerBank, trim_silence


def mk(text: str, dur: float) -> Filler:
    return Filler(text, b"\x00\x00" * int(24000 * dur), dur)


def test_pick_prefers_durations_close_to_expected_wait_and_never_repeats():
    bank = FillerBank([mk("a", 0.5), mk("b", 1.0), mk("c", 1.5), mk("d", 1.7), mk("e", 2.5)], expected_wait_s=1.6,
                      rng=random.Random(1))
    picks = [bank.pick() for _ in range(40)]
    assert "a" not in {p.text for p in picks}                      # la plus éloignée de 1,6 s n'est jamais choisie
    assert {"c", "d"} <= {p.text for p in picks}                   # les plus proches reviennent
    assert all(x is not y for x, y in pairwise(picks))


def test_observe_moves_expected_wait():
    bank = FillerBank([mk("a", 1.0)], expected_wait_s=2.0)
    bank.observe(1.0)
    assert 1.0 < bank.expected_wait_s < 2.0


def test_trim_silence_removes_leading_and_trailing_silence():
    tone = (8000 * np.sin(np.arange(24000) * 0.2)).astype(np.int16).tobytes()   # 1 s
    padded = b"\x00\x00" * 12000 + tone + b"\x00\x00" * 12000                   # 0,5 s de silence de chaque côté
    out = trim_silence(padded)
    assert 24000 * 2 <= len(out) < 24000 * 2 + 2 * 24000 * 0.12 * 2

import numpy as np

from src.audio.noise import rms
from src.vad.endpointer import VadConfig
from src.vad.eot_heuristic import internal_pauses, looks_incomplete, prefix_at
from src.vad.scan import EchoCase, classify_barge, clip_probs, end_of_turn_after_barge, scan

SR = 16000
LOUD = 3000.0


class EnergyVAD:
    """VAD factice : parole si la trame est assez forte (indépendant du niveau au-delà du seuil, comme Silero sur voix propre)."""
    def reset(self):
        pass

    def prob(self, frame):
        return 0.95 if rms(np.frombuffer(frame, np.int16).astype(float)) > 20 else 0.0


def tone(seconds, amp=LOUD):
    return amp * np.sin(2 * np.pi * 220 * np.arange(int(seconds * SR)) / SR)


def test_render_places_echo_after_delay_and_user_on_top():
    case = EchoCase(tone(1.0), agent_start=SR // 2, gain=0.1, delay_ms=100, user=tone(0.5), user_start=SR, tail=0)
    x = case.render()
    assert rms(x[:SR // 2 + 1600 - 10]) == 0                       # rien avant l'agent + retard
    assert 0.05 * LOUD < rms(x[SR // 2 + 2000:SR - 100]) < 0.1 * LOUD   # écho seul : niveau = gain
    assert rms(x[SR + 100:SR + 3000]) > 0.5 * LOUD                 # utilisateur par-dessus
    assert rms(EchoCase(tone(1.0), 0, None, 100, tail=0).render()) == 0   # contrôle sans écho


def test_agent_stop_cuts_echo_after_the_delay():
    case = EchoCase(tone(2.0), 0, 1.0, 100, tail=SR)
    x = case.render(agent_stop=SR // 2)
    assert rms(x[SR // 2 + 1600 + 200:]) == 0 and rms(x[SR // 2:SR // 2 + 1000]) > 0


def test_classify_barge_distinguishes_echo_trigger_from_real_interruption():
    case = EchoCase(tone(3.0), 0, 0.1, 100, tone(1.0), user_start=SR)       # l'utilisateur commence à 1000 ms
    assert classify_barge(None, case, 800) == "missed" and classify_barge(600.0, case, 800) == "false"
    assert classify_barge(1500.0, case, 800) == "ok" and classify_barge(2200.0, case, 800) == "late"
    alone = EchoCase(tone(3.0), 0, 0.1, 100)
    assert classify_barge(None, alone, 800) == "none" and classify_barge(900.0, alone, 800) == "false"


def test_echo_loud_enough_triggers_a_false_barge_in_and_quiet_echo_does_not():
    vcfg = VadConfig()
    loud = clip_probs(EnergyVAD(), EchoCase(tone(3.0), 0, 0.5, 100).render())
    quiet = clip_probs(EnergyVAD(), EchoCase(tone(3.0), 0, 0.001, 100).render())
    assert scan(loud, vcfg, strict_prob=0.6, barge_ms=300)[1] is not None
    assert scan(quiet, vcfg, strict_prob=0.6, barge_ms=300)[1] is None


def test_strict_until_ends_the_raised_threshold():
    probs = [0.5] * 40                                  # sous le seuil relevé (0,6) mais au-dessus du seuil normal (0,5)
    assert scan(probs, VadConfig(), strict_prob=0.6, barge_ms=300)[1] is None
    assert scan(probs, VadConfig(), strict_prob=0.6, barge_ms=300, strict_until_ms=100)[1] is not None


def test_end_of_turn_after_barge_reports_cut_and_delay():
    user = np.concatenate([tone(0.6), np.zeros(int(0.9 * SR)), tone(0.6)])   # pause interne de 900 ms (l'écho couvre le début)
    case = EchoCase(tone(4.0), 0, 0.5, 100, user, user_start=SR)
    r = end_of_turn_after_barge(EnergyVAD(), case, VadConfig(), 0.6, at_ms=1400.0, agent_stop_ms=300, end_silences=[450, 1000])
    assert r[450]["cut"] and not r[1000]["cut"]
    assert r[1000]["ended"] and 900 <= r[1000]["end_delay_ms"] <= 1200


def test_internal_pauses_and_incomplete_heuristic():
    cfg = VadConfig()
    probs = [0.9] * 20 + [0.0] * 10 + [0.9] * 20 + [0.0] * 5
    pauses, total = internal_pauses(probs, cfg, min_pause_ms=250)
    assert len(pauses) == 1 and pauses[0].dur_ms == 320 and abs(pauses[0].frac - 0.4) < 0.01 and total == 50 * 32
    assert prefix_at("Bonjour, tu m'entends bien ?", 0.25) == "Bonjour,"
    last, trailing = {"et", "de", "un"}, (",", "…")
    assert looks_incomplete("Je voudrais un", last, trailing, 3) and looks_incomplete("Alors,", last, trailing, 3)
    assert looks_incomplete("Oui", last, trailing, 3) and not looks_incomplete("Quelle heure est-il ?", last, trailing, 3)

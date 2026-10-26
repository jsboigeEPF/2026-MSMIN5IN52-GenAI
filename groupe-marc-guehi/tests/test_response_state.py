from src.orchestrator.response import ResponseState


def test_reserve_shifts_the_offset_and_returns_ranks():
    r = ResponseState()
    assert r.reserve("Alors,", 0.6) == 0 and r.off == 1 and r.spoken == ["Alors,"]
    assert r.add_chunk("La réponse.") == 1 and r.durs == [0.6, 0.0]


def test_waiting_tracks_first_sound_and_end_of_wait():
    r = ResponseState()
    r.waiting(0.5, 1.0)
    r.waiting(1.2, 0.5)            # enchaînée avant la fin de la précédente (1,5 s) : elle repart de 1,5 s
    assert r.lead == [0.5, 2.0]
    r.waiting(3.0, 0.5)            # après un silence : repart de son propre début
    assert r.lead == [0.5, 3.5]


def test_played_s_uses_first_audio_and_never_goes_negative():
    r = ResponseState()
    assert r.played_s(10.0, 0.05) == 0.0
    r.first_audio_at = 9.0
    assert r.played_s(10.0, 0.05) == 0.95 and r.played_s(9.01, 0.05) == 0.0


def test_played_text_uses_completion_flag():
    r = ResponseState()
    r.add_chunk("Bonjour tout le monde.", 2.0)
    assert r.played_text(10.0) == ("Bonjour tout le monde.", True)   # tour inachevé : marqué tronqué
    r.turn_complete = True
    assert r.played_text(10.0) == ("Bonjour tout le monde.", False)


def test_reset_clears_everything_but_keeps_the_same_object():
    r = ResponseState()
    r.reserve("x", 1.0)
    r.first_audio_at, r.first_content_s, r.turn_complete, r.user_recorded, r.searched = 1.0, 2.0, True, True, True
    r.current_audio, r.lead = (b"a", 16000), [0.0, 1.0]
    same = r
    r.reset()
    assert r is same and r == ResponseState()

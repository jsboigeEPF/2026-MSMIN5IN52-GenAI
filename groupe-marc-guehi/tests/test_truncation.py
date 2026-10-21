from src.orchestrator.truncation import heard_text, history_content

TEXTS = ["Alors,", "Voici la première phrase.", "Et voici la seconde phrase un peu plus longue."]
DURS = [0.6, 2.0, 3.0]


def test_everything_heard_when_playback_covers_all():
    heard, trunc = heard_text(TEXTS, DURS, played_s=10.0, complete=True)
    assert heard == " ".join(TEXTS) and not trunc


def test_interrupt_inside_first_sentence_cuts_at_word_boundary():
    heard, trunc = heard_text(TEXTS, DURS, played_s=1.6, complete=True)  # accroche finie, ~50 % de la phrase 1
    assert heard.startswith("Alors, Voici la") and heard.endswith("…") and trunc
    assert "seconde" not in heard


def test_chunks_without_audio_are_not_counted_as_heard():
    heard, trunc = heard_text(TEXTS, [0.6, 2.0, 0.0], played_s=5.0, complete=False)
    assert heard == "Alors, Voici la première phrase." and trunc


def test_interrupt_before_any_audio_gives_nothing():
    assert heard_text(["Bonjour."], [0.0], played_s=0.0, complete=False) == ("", True)


def test_history_content_marks_truncation_only_when_something_was_heard():
    assert history_content("Alors, voici…", True).endswith("[interrompu par l'utilisateur]")
    assert history_content("Phrase complète.", False) == "Phrase complète."
    assert history_content("", True) == ""

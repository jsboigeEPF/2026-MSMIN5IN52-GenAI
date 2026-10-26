from src.orchestrator.chunker import SentenceChunker


def feed_all(text: str, step: int = 3, **kw) -> list[str]:
    c, out = SentenceChunker(**kw), []
    for i in range(0, len(text), step):
        out += c.feed(text[i:i + step])
    return out + c.flush()


def test_first_chunk_cut_at_first_comma():
    out = feed_all("Bien sûr, je peux t'aider. Commence par une question simple au public.")
    assert out[0] == "Bien sûr,"
    assert out[1] == "je peux t'aider. Commence par une question simple au public."


def test_short_first_clause_waits_for_min_length():
    assert feed_all("Oui, je t'entends bien. Dis-moi tout.", first_min=8)[0] == "Oui, je t'entends bien."


def test_flush_returns_unterminated_tail():
    assert feed_all("Une phrase sans point final")[-1] == "Une phrase sans point final"


def test_no_text_lost():
    text = "Alors, voyons. Il y a trois étapes : d'abord, ensuite, enfin. Voilà !"
    assert " ".join(feed_all(text)).split() == text.split()

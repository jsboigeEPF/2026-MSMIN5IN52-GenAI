"""Lot B3/B4 : transcriptions vides ou hallucinées, et historique (jamais deux `user` d'affilée, trim, réponse vide, coupure par longueur)."""
import asyncio
from dataclasses import replace

import httpx
import pytest

from src.fakes import FakeLLM, FakeSTT, FakeTTS
from src.interfaces import LengthCut
from src.llm.openrouter import OpenRouterLLM
from src.metrics.recorder import Recorder
from src.orchestrator.history import History, append_assistant, append_user, cut_at_sentence
from src.orchestrator.state import State
from src.orchestrator.turn import IgnoredTranscript, run_turn
from src.stt.noise import noise_reason
from tests.test_llm_search import delta, sse
from tests.test_session_search import BASE, bank, feed, make, types, until

PHRASES = BASE["stt"]["ignore_phrases"]


# ---------- B3 : détection du bruit ----------
@pytest.mark.parametrize("text,reason", [
    ("", "empty"), ("   ", "empty"), ("...", "no_words"), ("♪", "no_words"), ("♪ la la la ♪", "annotation"), ("?!", "no_words"), ("[Musique]", "annotation"),
    ("(rires)", "annotation"), ("*bruit*", "annotation"),
    ("Sous-titres réalisés par la communauté d'Amara.org", "hallucination"),
    ("sous-titres réalisés par la communauté d’amara.org.", "hallucination"),
    ("Merci d'avoir regardé cette vidéo !", "hallucination"),
])
def test_noise_is_detected(text, reason):
    assert noise_reason(text, PHRASES) == reason


@pytest.mark.parametrize("text", ["Merci.", "Bonjour", "Non", "Il y a 2 chats", "Merci d'avoir regardé mon exposé hier ?",
                                  "Sous-titres de quoi parles-tu"])
def test_real_speech_is_kept(text):
    assert noise_reason(text, PHRASES) is None


def test_hallucination_list_is_config_driven():
    assert noise_reason("Abonnez-vous", ()) is None
    assert noise_reason("Abonnez-vous", ["abonnez-vous."]) == "hallucination"


async def test_run_turn_raises_before_any_llm_call_or_history_change():
    llm, history = FakeLLM(on_event=Recorder()), []
    calls = []
    orig = llm.stream
    llm.stream = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    with pytest.raises(IgnoredTranscript) as e:
        await run_turn(stt=FakeSTT(" "), llm=llm, tts=FakeTTS(), history=history, system_prompt="s", rec=Recorder(),
                       audio=(b"\0\0" * 160, 16000))
    assert e.value.reason == "empty" and calls == [] and history == []


async def test_run_turn_text_input_is_never_filtered():
    h: list[dict] = []
    r = await run_turn(stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS(), history=h, system_prompt="s", rec=Recorder(),
                       user_text="Merci d'avoir regardé cette vidéo", ignore_phrases=PHRASES)
    assert r.answer and h[0]["content"] == "Merci d'avoir regardé cette vidéo"


# ---------- B3 : session ----------
async def _speak_once(s, vad):
    await feed(s, vad, 0.9, 20)
    await feed(s, vad, 0.0, 8)


@pytest.mark.parametrize("heard", ["", "Sous-titres réalisés par la communauté d'Amara.org", "...", "[Musique]"])
async def test_noise_transcript_skips_the_llm_and_returns_to_listening(heard):
    s, vad, sent = make(FakeSTT(heard))
    asked = []
    orig = s.deps.llm.stream
    s.deps.llm.stream = lambda *a, **k: (asked.append(1), orig(*a, **k))[1]
    await _speak_once(s, vad)
    await until(lambda: "ignored" in types(sent) and s.state is State.LISTENING)
    assert asked == [] and s.history.messages == []
    assert "transcript" not in types(sent) and "metrics" not in types(sent) and "audio_end" not in types(sent)
    assert s.deps.rec.last("turn_ignored") is not None


async def test_noise_after_an_opening_filler_lets_it_finish_then_listens():
    s, vad, sent = make(FakeSTT(""))
    s.deps.fillers = bank("Alors,", 0.3)
    await _speak_once(s, vad)
    await until(lambda: "ignored" in types(sent) and "audio_end" in types(sent))
    assert s.state is State.SPEAKING                      # l'accroche est encore en cours de lecture côté client
    assert any(isinstance(m, tuple) and m[0] == "audio" for m in sent)   # l'accroche a bien été envoyée
    await s.on_playback_ended()                          # le client confirme la fin de lecture
    assert s.state is State.LISTENING and s.history.messages == []
    await feed(s, vad, 0.0, 2)


async def test_ignored_transcripts_are_not_stt_failures():
    s, vad, sent = make(FakeSTT(""), text_fallback_after=2)
    for _ in range(3):
        n = types(sent).count("ignored")
        await _speak_once(s, vad)
        await until(lambda n=n: types(sent).count("ignored") > n and s.state is State.LISTENING)
        await asyncio.sleep(0.05)
    assert not s.text_only


async def test_next_utterance_after_noise_is_answered_normally():
    class Seq(FakeSTT):
        n = 0

        async def transcribe(self, pcm16, sample_rate):
            self.n += 1
            return "" if self.n == 1 else "ma vraie question"

    s, vad, sent = make(Seq())
    await _speak_once(s, vad)
    await until(lambda: "ignored" in types(sent) and s.state is State.LISTENING)
    await asyncio.sleep(0.6)                             # fin de la période stricte après le retour à l'écoute
    await _speak_once(s, vad)
    await until(lambda: "audio_end" in types(sent))
    assert [m["role"] for m in s.history.messages] == ["user", "assistant"]
    assert s.history.messages[0]["content"] == "ma vraie question"


def test_session_config_reads_ignore_phrases():
    from src.orchestrator.session_config import SessionConfig
    assert SessionConfig.from_dict(BASE).ignore_phrases == tuple(PHRASES)
    assert replace(SessionConfig.from_dict(BASE), ignore_phrases=()).ignore_phrases == ()


# ---------- B4 : historique ----------
def test_append_user_never_leaves_two_user_messages_in_a_row():
    m: list[dict] = []
    append_user(m, "Quelle heure est-il ?")
    append_user(m, "Et la météo ?")           # la 1re question est restée sans réponse (interruption)
    assert m == [{"role": "user", "content": "Quelle heure est-il ? Et la météo ?"}]
    append_assistant(m, "Il est midi.")
    append_user(m, "Merci")
    assert [x["role"] for x in m] == ["user", "assistant", "user"]


def test_empty_answers_are_not_recorded():
    m: list[dict] = [{"role": "user", "content": "q"}]
    append_assistant(m, "   ")
    append_assistant(m, "", cut_by_length=True)
    assert len(m) == 1


def test_cut_at_sentence_keeps_whole_sentences_only():
    assert cut_at_sentence("Il fait beau. Il y a du so") == "Il fait beau."
    assert cut_at_sentence("Un. Deux ! Trois ? Quat") == "Un. Deux ! Trois ?"
    assert cut_at_sentence("Pas de ponctuation du tout") == "Pas de ponctuation du tout"
    assert cut_at_sentence("Bonjour… Je suis la") == "Bonjour…"
    assert cut_at_sentence("3.5 degrés ici") == "3.5 degrés ici"       # un point sans espace n'est pas une fin de phrase


def _hist(max_messages, *roles):
    h = History(max_messages)
    h.messages = [{"role": r, "content": f"{r}{i}"} for i, r in enumerate(roles)]
    return h


def test_trim_with_zero_keeps_no_memory():
    h = _hist(0, "user", "assistant", "user")
    h.trim()
    assert h.messages == []


def test_trim_starts_on_a_user_message_and_keeps_the_limit():
    h = _hist(3, "user", "assistant", "user", "assistant", "user", "assistant")
    h.trim()
    assert [m["role"] for m in h.messages] == ["user", "assistant"]    # 3 derniers = assistant, user, assistant → on retire l'assistant de tête
    h = _hist(4, "user", "assistant", "user", "assistant", "user")
    h.trim()
    assert [m["content"] for m in h.messages] == ["user2", "assistant3", "user4"]   # l'assistant de tête est retiré
    h = _hist(20, "user", "assistant")
    h.trim()
    assert len(h.messages) == 2


def test_trim_with_only_assistant_messages_empties_the_history():
    h = _hist(2, "assistant", "assistant")
    h.trim()
    assert h.messages == []


def test_prefix_last_never_rewrites_a_user_message():
    h = _hist(10, "user")
    h.prefix_last(["Alors,"])
    assert h.messages[-1] == {"role": "assistant", "content": "Alors,"} and h.messages[0]["content"] == "user0"
    h = _hist(10, "user", "assistant")
    h.prefix_last(["Alors,"])
    assert h.messages[-1]["content"] == "Alors, assistant1"
    h = _hist(10, "user")
    h.prefix_last([])
    assert [m["role"] for m in h.messages] == ["user"]


async def test_run_turn_with_empty_answer_adds_no_assistant_message():
    h: list[dict] = []
    r = await run_turn(stt=FakeSTT(), llm=FakeLLM(reply=""), tts=FakeTTS(), history=h, system_prompt="s", rec=Recorder(), user_text="Salut")
    assert r.answer == "" and [m["role"] for m in h] == ["user"]


async def test_run_turn_after_an_unanswered_question_merges_the_two_questions():
    h = [{"role": "user", "content": "première"}]
    await run_turn(stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS(), history=h, system_prompt="s", rec=Recorder(), user_text="seconde")
    assert [m["role"] for m in h] == ["user", "assistant"] and h[0]["content"] == "première seconde"


class LengthLLM(FakeLLM):
    async def stream(self, messages, max_tokens=None, allow_search=True):
        for w in ("Il fait beau. ", "Il y a du "):
            yield w
        yield LengthCut()


async def test_length_cut_keeps_whole_sentences_in_history_but_speaks_everything():
    h: list[dict] = []
    r = await run_turn(stt=FakeSTT(), llm=LengthLLM(), tts=FakeTTS(), history=h, system_prompt="s", rec=Recorder(), user_text="Salut")
    assert h[-1]["content"] == "Il fait beau." and r.answer == "Il fait beau. Il y a du"


async def test_llm_reports_finish_reason_length():
    body = sse({"choices": [{"delta": {"content": "Il fait beau. Il y a du "}, "finish_reason": None}]},
               {"choices": [{"delta": {}, "finish_reason": "length"}]})
    events = []
    llm = OpenRouterLLM(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))), "http://x", "k", "m",
                        on_event=lambda e, **k: events.append(e))
    out = [d async for d in llm.stream([{"role": "user", "content": "q"}])]
    assert isinstance(out[-1], LengthCut) and "llm_length" in events
    body = sse(delta(content="Fini."), {"choices": [{"delta": {}, "finish_reason": "stop"}]})
    llm = OpenRouterLLM(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))), "http://x", "k", "m")
    assert not any(isinstance(d, LengthCut) for d in [d async for d in llm.stream([{"role": "user", "content": "q"}])])


# ---------- suites de l'audit du lot B ----------
async def test_interrupting_the_filler_of_an_ignored_turn_leaves_history_and_carry_untouched():
    s, vad, sent = make(FakeSTT(""))
    s.deps.fillers = bank("Hmm, voyons.", 2.0)
    s.history.messages[:] = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "Réponse complète n°1."}]
    await _speak_once(s, vad)
    await until(lambda: "ignored" in types(sent) and s.state is State.SPEAKING)
    await s.interruptions.barge_in()                       # l'utilisateur parle par-dessus l'accroche
    assert s.history.messages == [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "Réponse complète n°1."}]
    assert s.interruptions.carry is None and s.state is State.LISTENING


@pytest.mark.parametrize("text", ["(Oui) je veux (peut-être)", "[Oui] bien sûr [non]", "*Oui* je pense *peut-être*"])
def test_text_with_inner_brackets_is_not_an_annotation(text):
    assert noise_reason(text, PHRASES) is None


def test_trim_with_a_single_message_window():
    h = _hist(1, "user", "assistant")
    h.trim()
    assert h.messages == []         # le dernier message est une réponse : une fenêtre d'un message ne peut pas commencer par `user`
    h = _hist(1, "assistant", "user")
    h.trim()
    assert [m["role"] for m in h.messages] == ["user"]

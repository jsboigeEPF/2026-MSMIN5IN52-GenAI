import asyncio
from dataclasses import replace
from pathlib import Path

import yaml

from src.fakes import FakeLLM, FakeSTT, FakeTTS
from src.interfaces import SearchNotice
from src.metrics.recorder import Recorder
from src.orchestrator.session import ConversationSession
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.orchestrator.state import State
from src.tts.fillers import Filler, FillerBank
from src.vad.endpointer import Endpointer, VadConfig

FRAME = b"\x01\x00" * 512
BASE = yaml.safe_load(Path("configs/base.yaml").read_text(encoding="utf-8"))
LISTENING = State.LISTENING


class ScriptedVAD:
    p = 0.0

    def reset(self):
        pass

    def prob(self, frame):
        return self.p


class CountingSTT(FakeSTT):
    def __init__(self, texts=("première", "seconde"), fail=False):
        super().__init__()
        self.texts, self.calls, self.fail = list(texts), 0, fail

    async def transcribe(self, pcm16, sample_rate):
        self.calls += 1
        n = self.calls
        await asyncio.sleep(0.05)
        if self.fail:
            raise RuntimeError("stt down")
        return self.texts[min(n, len(self.texts)) - 1]


def make(stt=None, search_fillers=None, wait_fillers=None, **cfg_over):
    sent: list = []

    async def send_json(m):
        sent.append(m)

    async def send_audio(b):
        sent.append(("audio", len(b)))

    vad, rec = ScriptedVAD(), Recorder()
    deps = SessionDeps(
        stt=stt or FakeSTT("ma question"), llm=FakeLLM("Il fait beau. Vraiment très beau aujourd'hui.", on_event=rec), tts=FakeTTS(),
        vad=vad, endpointer=Endpointer(VadConfig(end_silence_ms=128)), rec=rec, system_prompt="s", send_json=send_json,
        send_audio=send_audio, search_fillers=search_fillers, wait_fillers=wait_fillers)
    base = replace(SessionConfig.from_dict(BASE), strict_transitions=True, speculative_ms=0, text_fallback_after=0,
                   search_wait_after_s=0.0)
    s = ConversationSession(deps, replace(base, **cfg_over))
    return s, vad, sent


async def feed(s, vad, p, n):
    vad.p = p
    for _ in range(n):
        await s.on_mic(FRAME)
        await asyncio.sleep(0)


async def until(cond, timeout=5.0):
    t = 0.0
    while not cond():
        await asyncio.sleep(0.01)
        t += 0.01
        assert t < timeout, "condition jamais remplie"


def types(sent):
    return [m["type"] for m in sent if isinstance(m, dict)]


def bank(text="Je regarde ça.", dur=0.3):
    return FillerBank([Filler(text, b"\x00\x00" * int(24000 * dur), dur)])


async def test_search_plays_waiting_phrase_and_keeps_it_in_history():
    s, _, sent = make(search_fillers=bank())
    await s.on_text("Quelle est la météo ?")
    await until(lambda: "audio_end" in types(sent))
    chunks = [m["text"] for m in sent if isinstance(m, dict) and m["type"] == "agent_chunk"]
    assert chunks[0] == "Je regarde ça." and "searching" in types(sent)
    metrics = next(m for m in sent if isinstance(m, dict) and m["type"] == "metrics")
    assert metrics["searched"] and metrics["search_ms"] == 300.0 and metrics["filler"] == "Je regarde ça."
    assert metrics["first_sound_ms"] < metrics["content_ms"]
    assert s.history[-1]["content"].startswith("Je regarde ça. Il fait beau.")


async def test_search_without_waiting_phrase_still_answers():
    s, _, sent = make()
    await s.on_text("Quelle est la météo ?")
    await until(lambda: "audio_end" in types(sent))
    assert "searching" in types(sent)
    assert next(m for m in sent if isinstance(m, dict) and m["type"] == "metrics")["filler"] is None


async def test_speculative_stt_is_reused_at_end_of_turn():
    stt = CountingSTT()
    s, vad, sent = make(stt, speculative_ms=64)
    await feed(s, vad, 0.9, 20)
    await feed(s, vad, 0.0, 8)
    await until(lambda: "audio_end" in types(sent))
    assert stt.calls == 1
    assert {"type": "transcript", "role": "user", "text": "première"} in sent


async def test_speculative_stt_dropped_when_speech_resumes():
    stt = CountingSTT()
    s, vad, sent = make(stt, speculative_ms=64)
    await feed(s, vad, 0.9, 20)
    await feed(s, vad, 0.0, 3)   # pause > 64 ms : transcription lancée
    await feed(s, vad, 0.9, 10)  # la parole reprend : jetée
    await feed(s, vad, 0.0, 8)
    await until(lambda: "audio_end" in types(sent))
    assert stt.calls == 2
    assert {"type": "transcript", "role": "user", "text": "seconde"} in sent


async def test_two_stt_failures_switch_to_text_mode_and_voice_can_be_retried():
    s, vad, sent = make(CountingSTT(fail=True), text_fallback_after=2)
    for _ in range(2):
        await feed(s, vad, 0.9, 20)
        await feed(s, vad, 0.0, 8)
        await until(lambda: s.state == LISTENING and s.tasks.turn.done())
        await asyncio.sleep(0.2)
    assert {"type": "fallback", "mode": "text", "reason": "stt"} in sent and s.text_only
    n = len(sent)
    await feed(s, vad, 0.9, 20)  # micro ignoré
    assert len(sent) == n
    await s.on_voice_retry()
    assert not s.text_only and {"type": "fallback", "mode": "voice"} in sent



class BrokenTTS:
    async def stream(self, text):
        raise RuntimeError("tts down")
        yield b""  # pragma: no cover


class SlowSearchLLM(FakeLLM):
    """Recherche qui traîne : 0,5 s entre l'annonce de recherche et le début de la réponse."""

    async def stream(self, messages, max_tokens=None, allow_search=True):
        yield SearchNotice("météo Lyon", "Je regarde la météo à Lyon.")
        await asyncio.sleep(0.5)
        yield "Il fait beau. "
        yield "Vraiment très beau aujourd'hui."


class BrokenAnnounceTTS(FakeTTS):
    async def stream(self, text):
        if text.startswith("Je regarde"):
            raise RuntimeError("tts down")
        async for c in super().stream(text):
            yield c


def agent_chunks(sent):
    return [m["text"] for m in sent if isinstance(m, dict) and m["type"] == "agent_chunk"]


async def test_search_announce_is_spoken_before_the_answer():
    s, _, sent = make(search_fillers=bank())
    s.deps.llm = SlowSearchLLM()
    await s.on_text("Quelle est la météo à Lyon ?")
    await until(lambda: "audio_end" in types(sent))
    assert agent_chunks(sent)[0] == "Je regarde la météo à Lyon."
    assert s.history[-1]["content"].startswith("Je regarde la météo à Lyon. Il fait beau.")
    metrics = next(m for m in sent if isinstance(m, dict) and m["type"] == "metrics")
    assert metrics["filler"] == "Je regarde la météo à Lyon." and metrics["first_sound_ms"] < metrics["content_ms"]


async def test_announce_falls_back_to_generic_phrase_when_tts_fails():
    s, _, sent = make(search_fillers=bank("Je regarde ça."))
    s.deps.llm, s.deps.tts = SlowSearchLLM(), BrokenAnnounceTTS()
    await s.on_text("Quelle est la météo à Lyon ?")
    await until(lambda: "audio_end" in types(sent))
    assert agent_chunks(sent)[0] == "Je regarde ça." and "Je regarde la météo à Lyon." not in s.history[-1]["content"]


async def test_second_phrase_when_search_is_slow():
    s, _, sent = make(search_fillers=bank(), wait_fillers=bank("Encore un instant.", 0.2), search_wait_after_s=0.2)
    s.deps.llm = SlowSearchLLM()
    await s.on_text("Quelle est la météo à Lyon ?")
    await until(lambda: "audio_end" in types(sent))
    assert agent_chunks(sent)[:2] == ["Je regarde la météo à Lyon.", "Encore un instant."]
    assert s.history[-1]["content"].startswith("Je regarde la météo à Lyon. Encore un instant. Il fait beau.")


async def test_no_second_phrase_when_answer_comes_quickly():
    s, _, sent = make(search_fillers=bank(), wait_fillers=bank("Encore un instant.", 0.2), search_wait_after_s=2.0)
    s.deps.llm = SlowSearchLLM()
    await s.on_text("Quelle est la météo à Lyon ?")
    await until(lambda: "audio_end" in types(sent))
    await asyncio.sleep(0.1)
    assert "Encore un instant." not in agent_chunks(sent)


async def test_tts_outage_keeps_text_and_tells_the_client():
    s, _, sent = make()
    s.deps.tts = BrokenTTS()
    await s.on_text("Bonjour")
    await until(lambda: "audio_end" in types(sent))
    assert "tts_down" in types(sent) and agent_chunks(sent) and s.state == LISTENING


async def test_waiting_phrases_are_sent_to_the_client_exactly_once():
    s, _, sent = make(search_fillers=bank(), wait_fillers=bank("Encore un instant.", 0.2), search_wait_after_s=0.2)
    s.deps.llm = SlowSearchLLM()
    await s.on_text("Quelle est la météo à Lyon ?")
    await until(lambda: "audio_end" in types(sent))
    chunks = agent_chunks(sent)
    assert chunks[:2] == ["Je regarde la météo à Lyon.", "Encore un instant."]
    assert len(chunks) == len(set(chunks))   # une accroche ne doit jamais repartir avec la réponse (sous-titres dupliqués)

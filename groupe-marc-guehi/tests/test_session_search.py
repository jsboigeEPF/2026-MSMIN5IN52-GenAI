import asyncio

from src.fakes import FakeLLM, FakeSTT, FakeTTS
from src.metrics.recorder import Recorder
from src.orchestrator.session import LISTENING, ConversationSession
from src.tts.fillers import Filler, FillerBank
from src.vad.endpointer import Endpointer, VadConfig

FRAME = b"\x01\x00" * 512


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


def make(stt=None, search_fillers=None, **kw):
    sent: list = []

    async def send_json(m):
        sent.append(m)

    async def send_audio(b):
        sent.append(("audio", len(b)))

    vad, rec = ScriptedVAD(), Recorder()
    s = ConversationSession(
        stt=stt or FakeSTT("ma question"), llm=FakeLLM("Il fait beau. Vraiment très beau aujourd'hui.", on_event=rec), tts=FakeTTS(),
        vad=vad, endpointer=Endpointer(VadConfig(end_silence_ms=128)), rec=rec, system_prompt="s", send_json=send_json,
        send_audio=send_audio, search_fillers=search_fillers, **kw)
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
        await until(lambda: s.state == LISTENING and s._task.done())
        await asyncio.sleep(0.2)
    assert {"type": "fallback", "mode": "text", "reason": "stt"} in sent and s.text_only
    n = len(sent)
    await feed(s, vad, 0.9, 20)  # micro ignoré
    assert len(sent) == n
    await s.on_voice_retry()
    assert not s.text_only and {"type": "fallback", "mode": "voice"} in sent


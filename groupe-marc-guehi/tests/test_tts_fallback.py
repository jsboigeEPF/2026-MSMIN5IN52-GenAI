import pytest

from src.fakes import FakeLLM, FakeSTT, FakeTTS
from src.fallback.tts import FallbackTTS
from src.metrics.recorder import Recorder
from src.orchestrator.turn import run_turn


class BrokenTTS:
    async def stream(self, text):
        raise RuntimeError("tts down")
        yield b""  # pragma: no cover  (fait de la méthode un générateur asynchrone)

    async def warmup(self):
        pass


class CutTTS:
    """Livre un morceau puis tombe en panne en plein flux."""

    async def stream(self, text):
        yield b"\x01\x00" * 100
        raise RuntimeError("coupé")

    async def warmup(self):
        pass


async def collect(tts, text="Bonjour."):
    return [c async for c in tts.stream(text)]


async def test_secondary_used_when_primary_fails_before_audio():
    events = []
    tts = FallbackTTS(BrokenTTS(), FakeTTS(), on_event=lambda e, **k: events.append(e))
    assert await collect(tts)
    assert "tts_fallback" in events


async def test_no_switch_once_audio_has_started():
    with pytest.raises(RuntimeError):
        await collect(FallbackTTS(CutTTS(), FakeTTS()))


async def test_both_failing_raises():
    with pytest.raises(RuntimeError):
        await collect(FallbackTTS(BrokenTTS(), BrokenTTS()))


async def test_turn_survives_tts_outage_and_keeps_the_text():
    rec = Recorder()
    history: list[dict] = []
    r = await run_turn(stt=FakeSTT(), llm=FakeLLM("Il fait beau. Vraiment très beau."), tts=BrokenTTS(), history=history,
                       system_prompt="s", rec=rec, user_text="salut")
    assert r.tts_failed and "beau" in r.answer
    assert history[-1]["role"] == "assistant" and history[-1]["content"] == r.answer
    assert any(e == "tts_error" for e, _, _ in rec.events)

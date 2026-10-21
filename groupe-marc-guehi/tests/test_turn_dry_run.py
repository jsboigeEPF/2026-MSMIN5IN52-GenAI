from src.fakes import FakeLLM, FakeSTT, FakeTTS
from src.metrics.recorder import Recorder
from src.orchestrator.turn import run_turn


async def test_text_turn_with_fakes():
    rec = Recorder()
    history: list[dict] = []
    r = await run_turn(stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS(), history=history, system_prompt="sys", rec=rec,
                       user_text="Salut")
    assert r.answer and len(r.audio) >= 2
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert r.first_audio_ms <= r.total_ms


async def test_audio_turn_uses_stt():
    r = await run_turn(stt=FakeSTT("texte entendu"), llm=FakeLLM(), tts=FakeTTS(), history=[], system_prompt="s",
                       rec=Recorder(), audio=(b"\x00\x00" * 160, 16000))
    assert r.user_text == "texte entendu" and r.stt_ms is not None

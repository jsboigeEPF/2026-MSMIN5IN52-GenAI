import pytest

from src.fakes import FakeSTT
from src.fallback.stt import FallbackSTT


class BrokenSTT:
    async def transcribe(self, pcm16, sample_rate):
        raise RuntimeError("down")

    async def warmup(self):
        pass


async def test_secondary_used_when_primary_fails():
    events = []
    stt = FallbackSTT(BrokenSTT(), FakeSTT("secours"), on_event=lambda e, **k: events.append(e))
    assert await stt.transcribe(b"", 16000) == "secours"
    assert events == ["stt_fallback"]


async def test_primary_preferred():
    assert await FallbackSTT(FakeSTT("principal"), FakeSTT("secours")).transcribe(b"", 16000) == "principal"


async def test_both_failing_raises():
    with pytest.raises(RuntimeError):
        await FallbackSTT(BrokenSTT(), BrokenSTT()).transcribe(b"", 16000)

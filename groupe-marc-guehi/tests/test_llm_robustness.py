"""Lot B1/B2 : repli du LLM sur toute erreur réseau avant le 1er jeton, échéance du 1er jeton, délais configurables."""
import asyncio
import json

import httpx
import pytest

from src.llm.openrouter import LLMError, OpenRouterLLM
from tests.test_llm_search import ANSWER, delta, sse


def make(handler, alt="alt", **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    events = []
    llm = OpenRouterLLM(client, "http://x", "k", "main", alt, on_event=lambda e, **k: events.append((e, k)), **kw)
    return llm, events


async def collect(llm):
    return [d async for d in llm.stream([{"role": "user", "content": "bonjour"}])]


def model_of(req) -> str:
    return json.loads(req.content)["model"]


@pytest.mark.parametrize("exc", [httpx.ConnectError("refusé"), httpx.ReadTimeout("lent"), httpx.RemoteProtocolError("coupé"),
                                 httpx.ConnectTimeout("t")])
async def test_network_error_before_first_token_falls_back_to_alt_model(exc):
    seen = []

    def handler(req):
        seen.append(model_of(req))
        if model_of(req) == "main":
            raise exc
        return httpx.Response(200, content=ANSWER)

    llm, events = make(handler)
    assert "".join(await collect(llm)) == "Il fait 10 degrés."
    assert seen == ["main", "alt"]
    assert any(e == "llm_fallback" for e, _ in events)


async def test_all_models_failing_raises_llm_error_with_a_readable_message():
    def handler(req):
        raise httpx.ConnectError("refusé")

    llm, _ = make(handler)
    with pytest.raises(LLMError) as e:
        await collect(llm)
    assert "ConnectError" in str(e.value)


async def test_http_error_status_still_falls_back():
    def handler(req):
        return httpx.Response(500, content=b"boom") if model_of(req) == "main" else httpx.Response(200, content=ANSWER)

    llm, _ = make(handler)
    assert "".join(await collect(llm)) == "Il fait 10 degrés."


class SlowStream(httpx.AsyncByteStream):
    """Réponse 200 dont le premier octet n'arrive qu'après `delay` secondes."""

    def __init__(self, delay: float, body: bytes) -> None:
        self.delay, self.body = delay, body

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        yield self.body


async def test_first_token_deadline_switches_to_alt_model():
    def handler(req):
        if model_of(req) == "main":
            return httpx.Response(200, stream=SlowStream(5, ANSWER))
        return httpx.Response(200, content=ANSWER)

    llm, events = make(handler, first_token_timeout_s=0.1)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert "".join(await collect(llm)) == "Il fait 10 degrés."
    assert loop.time() - t0 < 2
    fb = [k for e, k in events if e == "llm_fallback"]
    assert fb and "premier jeton" in fb[0]["error"]


async def test_deadline_stops_after_the_first_token():
    """Une fois le premier jeton reçu, plus d'échéance (la fin du flux peut être lente)."""
    class TailSlow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse(delta(content="Bonjour "))[:-len(b"data: [DONE]\n\n")]
            await asyncio.sleep(0.3)
            yield b"data: [DONE]\n\n"

    llm, events = make(lambda req: httpx.Response(200, stream=TailSlow()), first_token_timeout_s=0.1)
    assert "".join(await collect(llm)) == "Bonjour "
    assert not any(e == "llm_fallback" for e, _ in events)


async def test_all_models_too_slow_raises_llm_error():
    llm, _ = make(lambda req: httpx.Response(200, stream=SlowStream(5, ANSWER)), first_token_timeout_s=0.05)
    with pytest.raises(LLMError):
        await collect(llm)


async def test_no_deadline_when_disabled():
    llm, _ = make(lambda req: httpx.Response(200, stream=SlowStream(0.2, ANSWER)), first_token_timeout_s=0)
    assert "".join(await collect(llm)) == "Il fait 10 degrés."


async def test_http_timeout_is_configurable():
    seen = []

    def handler(req):
        seen.append(req.extensions["timeout"])
        return httpx.Response(200, content=ANSWER)

    llm, _ = make(handler, timeout_s=7.5)
    await collect(llm)
    assert seen[0]["read"] == 7.5


async def test_midstream_error_after_first_token_is_not_retried():
    """Une fois du texte parti, changer de modèle donnerait une réponse incohérente : l'erreur remonte."""
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse(delta(content="Début "))[:-len(b"data: [DONE]\n\n")]
            raise httpx.ReadError("coupé")

    calls = []

    def handler(req):
        calls.append(model_of(req))
        return httpx.Response(200, stream=Broken())

    llm, _ = make(handler)
    with pytest.raises(httpx.ReadError):
        await collect(llm)
    assert calls == ["main"]



# ---------- STT / TTS : délais configurables ----------
from src.fakes import FakeSTT
from src.fallback.stt import FallbackSTT
from src.stt.school_whisper import SchoolWhisper, STTError
from src.tts.minimax import MiniMaxTTS
from src.tts.openrouter_pcm import PcmTTS


async def test_slow_stt_times_out_and_the_fallback_answers():
    async def slow(req):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"text": "trop tard"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    primary = SchoolWhisper(client, "http://x", "k", "m", retries=0, timeout_s=0.1)
    with pytest.raises(STTError, match="pas de réponse"):
        await primary.transcribe(b"\0\0" * 1600, 16000)
    assert await FallbackSTT(primary, FakeSTT("secours")).transcribe(b"\0\0" * 1600, 16000) == "secours"


async def test_stt_and_tts_network_timeouts_come_from_the_constructor():
    seen = []

    def handler(req):
        seen.append(req.extensions["timeout"]["read"])
        return httpx.Response(200, json={"text": "ok"}) if "transcriptions" in str(req.url) else httpx.Response(500, content=b"x")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await SchoolWhisper(client, "http://x", "k", "m", timeout_s=3.5).transcribe(b"\0\0" * 1600, 16000)
    for tts in (MiniMaxTTS(client, "http://x", "k", "m", "v", retries=0, timeout_s=4.5), PcmTTS(client, "http://x", "k", "m", "v", timeout_s=5.5)):
        with pytest.raises(Exception, match="HTTP 500"):
            async for _ in tts.stream("Bonjour"):
                pass
    assert seen == [3.5, 4.5, 5.5]


def test_factory_reads_the_timeouts_from_the_config(monkeypatch):
    from src.config import load_settings
    from src.factory import build_providers
    s = load_settings()
    s.cfg["llm"].update(timeout_s=11, first_token_timeout_s=3)
    s.cfg["stt"]["timeout_s"] = 12
    s.cfg["tts"]["timeout_s"] = 13
    object.__setattr__(s, "openrouter_api_key", "k")
    for f in ("stt_base_url", "stt_api_key", "llm_model", "tts_api_key", "tts_model"):
        object.__setattr__(s, f, "x")
    object.__setattr__(s, "tts_voices", ["v"])
    stt, llm, tts = build_providers(s, "v", httpx.AsyncClient(), lambda *a, **k: None, False)
    assert (llm.timeout_s, llm.first_token_timeout_s) == (11, 3)
    assert stt.primary.timeout_s == 12 and stt.secondary.timeout_s == 12
    primary = getattr(tts, "primary", tts)
    assert primary.timeout_s == 13


# ---------- suites de l'audit du lot B ----------
from src.interfaces import SearchNotice
from tests.test_llm_search import TOOL_CALL, FakeSearch


async def test_follow_up_request_after_a_web_search_has_no_first_token_deadline():
    """Après la recherche, `SearchNotice` est parti : pas de repli possible, donc pas d'échéance (régression vue à l'audit)."""
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, content=TOOL_CALL) if len(calls) == 1 else httpx.Response(200, stream=SlowStream(0.3, ANSWER))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OpenRouterLLM(client, "http://x", "k", "main", searcher=FakeSearch(), first_token_timeout_s=0.1)
    out = await collect(llm)
    assert isinstance(out[0], SearchNotice) and "".join(out[1:]) == "Il fait 10 degrés."


async def test_deadline_is_disarmed_by_a_tool_call_chunk():
    class SlowTail(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield TOOL_CALL[:-len(b"data: [DONE]\n\n")]
            await asyncio.sleep(0.3)
            yield b"data: [DONE]\n\n"

    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, stream=SlowTail()) if len(calls) == 1 else httpx.Response(200, content=ANSWER)

    llm = OpenRouterLLM(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "http://x", "k", "main", searcher=FakeSearch(),
                        first_token_timeout_s=0.1)
    assert isinstance((await collect(llm))[0], SearchNotice)

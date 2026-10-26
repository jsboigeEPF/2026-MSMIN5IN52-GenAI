"""Lot C : `playback_first_sample` (premier échantillon audible, annoncé par le client) et horloge monotonic_ns."""
import asyncio
import inspect
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from src.metrics import clock
from src.orchestrator.state import State
from src.transport.app import app
from tests.test_session_search import bank, feed, make, types, until


# ---------- C2 : horloge ----------
def test_clock_units():
    t0 = clock.now_ns()
    assert clock.elapsed_ms(t0, t0 + 1_500_000) == 1.5
    assert clock.elapsed_s(t0, t0 + 2_500_000_000) == 2.5
    assert clock.elapsed_ms(t0) >= 0 and isinstance(t0, int)


def test_no_perf_counter_left_in_src_or_scripts():
    for root in ("src", "scripts"):
        for f in pathlib.Path(root).rglob("*.py"):
            assert "perf_counter" not in f.read_text(encoding="utf-8"), f


def test_durations_are_still_reported_in_ms_not_ns():
    from src.fakes import FakeLLM, FakeSTT, FakeTTS
    from src.metrics.recorder import Recorder
    from src.orchestrator.turn import run_turn
    rec = Recorder()

    async def go():
        return await run_turn(stt=FakeSTT(), llm=FakeLLM(on_event=rec), tts=FakeTTS(on_event=rec), history=[], system_prompt="s",
                              rec=rec, user_text="Salut")
    r = asyncio.run(go())
    assert 0 < r.first_audio_ms <= r.total_ms < 5_000        # des ms, pas des ns ni des s
    assert 0 < next(d["ms"] for e, _, d in rec.events if e == "turn_done") < 5_000


# ---------- C1 : session ----------
async def _turn_with_audio(**kw):
    s, _, sent = make(**kw)
    await s.on_text("Bonjour")
    await until(lambda: "audio_end" in types(sent))
    return s, sent


def _events(s, name):
    return [d for e, _, d in s.deps.rec.events if e == name]


async def test_playback_started_traces_the_first_audible_sample_once():
    s, _ = await _turn_with_audio()
    s.on_playback_started(120.0)
    s.on_playback_started(500.0)                      # un seul par réponse
    (ev,) = _events(s, "playback_first_sample")
    assert ev["client_lead_ms"] == 120.0
    assert ev["e2e_ms"] >= 120.0                      # depuis le début du tour : au moins le délai annoncé par le client
    assert ev["send_to_audible_ms"] >= 120.0 and ev["e2e_ms"] >= ev["send_to_audible_ms"]


async def test_e2e_is_measured_from_the_turn_start_to_the_audible_instant():
    s, _ = await _turn_with_audio()
    r = s.resp
    s.on_playback_started(0.0)
    (ev,) = _events(s, "playback_first_sample")
    assert ev["e2e_ms"] == pytest.approx((r.audible_ns - r.t_detect) / 1e6, abs=0.1)
    assert ev["send_to_audible_ms"] == pytest.approx((r.audible_ns - r.first_audio_ns) / 1e6, abs=0.1)


async def test_playback_started_without_audio_sent_is_ignored():
    s, _, _ = make()
    s.on_playback_started(50.0)                       # rien n'a été envoyé
    assert _events(s, "playback_first_sample") == []


async def test_late_playback_started_after_an_interruption_is_ignored():
    s, _, _ = make()
    s.deps.fillers = bank("Hmm, voyons.", 2.0)
    await s.on_text("Bonjour")
    await until(lambda: s.state is State.SPEAKING and s.resp.first_audio_ns is not None)
    await s.interruptions.barge_in()
    s.on_playback_started(10.0)
    assert _events(s, "playback_first_sample") == []


async def test_each_response_gets_its_own_event():
    s, _ = await _turn_with_audio()
    s.on_playback_started(30.0)
    await s.on_playback_ended()
    assert s.state is State.LISTENING
    await s.on_text("Autre question")
    await until(lambda: s.resp.first_audio_ns is not None and s.resp.turn_complete)
    s.on_playback_started(40.0)
    assert [e["client_lead_ms"] for e in _events(s, "playback_first_sample")] == [30.0, 40.0]


async def test_vad_turn_traces_the_event_too():
    s, vad, sent = make()
    await feed(s, vad, 0.9, 20)
    await feed(s, vad, 0.0, 8)
    await until(lambda: "audio_end" in types(sent))
    s.on_playback_started(80.0)
    assert len(_events(s, "playback_first_sample")) == 1


# ---------- C1 : protocole WebSocket ----------
def _connect(client):
    return client.websocket_connect("/ws")


@pytest.fixture
def dry(monkeypatch):
    monkeypatch.setenv("VOICE_DRY_RUN", "1")


def _read_until_audio_end(ws):
    while True:
        m = ws.receive()
        if m.get("text") and json.loads(m["text"])["type"] == "audio_end":
            return


def _trace_events(tmp_trace_dir, name):
    out = []
    for f in pathlib.Path(tmp_trace_dir).glob("*.jsonl"):
        out += [r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines()) if r["event"] == name]
    return out


@pytest.mark.parametrize("payload,bad", [
    ({"type": "playback_started", "in_ms": 85}, False),
    ({"type": "playback_started", "in_ms": 12.5}, False),
    ({"type": "playback_started"}, True), ({"type": "playback_started", "in_ms": "vite"}, True),
    ({"type": "playback_started", "in_ms": -5}, True), ({"type": "playback_started", "in_ms": 10**9}, True),
    ({"type": "playback_started", "in_ms": True}, True), ({"type": "playback_started", "in_ms": None}, True),
])
def test_playback_started_over_websocket(dry, tmp_path, payload, bad):
    with TestClient(app) as client:
        app.state.settings.cfg["metrics"]["trace_dir"] = str(tmp_path)
        with _connect(client) as ws:
            ws.receive_text()
            ws.send_text(json.dumps({"type": "text", "text": "Bonjour"}))
            _read_until_audio_end(ws)
            ws.send_text(json.dumps(payload))
            ws.send_text(json.dumps({"type": "playback_ended"}))
            assert json.loads(ws.receive_text())["type"] == "state"      # la session vit toujours
    events = _trace_events(tmp_path, "playback_first_sample")
    assert len(events) == (0 if bad else 1)
    assert len(_trace_events(tmp_path, "ws_bad_message")) == (1 if bad else 0)
    if not bad:
        assert events[0]["data"]["client_lead_ms"] == round(float(payload["in_ms"]), 1)


def test_nan_in_ms_is_rejected(dry, tmp_path):
    with TestClient(app) as client:
        app.state.settings.cfg["metrics"]["trace_dir"] = str(tmp_path)
        with _connect(client) as ws:
            ws.receive_text()
            ws.send_text(json.dumps({"type": "text", "text": "Bonjour"}))
            _read_until_audio_end(ws)
            ws.send_text('{"type": "playback_started", "in_ms": NaN}')
            ws.send_text(json.dumps({"type": "playback_ended"}))
            ws.receive_text()
    assert _trace_events(tmp_path, "playback_first_sample") == []


# ---------- client ----------
def test_client_sends_playback_started_once_per_response_without_touching_stats():
    html = pathlib.Path("src/transport/static/index.html").read_text(encoding="utf-8")
    block = re.search(r"if \(playStart === null\) \{(.*?)\n  \}\n", html, re.DOTALL).group(1)
    assert "playback_started" in block and "outputLatency" in block and "in_ms" in block
    assert "const stats = window.__stats = { framesSent: 0, audioBytes: 0, chunksPlayed: 0, transcripts: [], states: [], errors: [], metrics: [], interrupts: 0, truncates: [] };" in html


def test_session_handler_is_synchronous_and_cheap():
    assert not inspect.iscoroutinefunction(__import__("src.orchestrator.session", fromlist=["x"]).ConversationSession.on_playback_started)

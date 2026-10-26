"""Lot A : sécurité et coûts du serveur (Origin, session unique, messages malformés, keepalive payant)."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.metrics.cost import CostLedger
from src.transport import app as app_mod
from src.transport.app import _keepalive, app, origin_allowed, usage_recorder


@pytest.fixture
def dry(monkeypatch):
    monkeypatch.setenv("VOICE_DRY_RUN", "1")


def _drain_turn(ws) -> list[str]:
    seen = []
    while True:
        msg = ws.receive()
        if msg.get("bytes") is not None:
            continue
        m = json.loads(msg["text"])
        seen.append(m["type"])
        if m["type"] == "audio_end":
            return seen


def _text_turn_works(ws) -> None:
    ws.send_text(json.dumps({"type": "text", "text": "Bonjour"}))
    assert "agent_chunk" in _drain_turn(ws)


# ---------- A2 : Origin ----------
def test_origin_allowed_rules():
    hosts = ["localhost", "127.0.0.1", "::1"]
    assert origin_allowed(None, hosts)
    assert origin_allowed("http://localhost:8000", hosts)
    assert origin_allowed("http://[::1]:8000", hosts)
    assert not origin_allowed("https://evil.example", hosts)
    assert not origin_allowed("http://localhost.evil.example", hosts)
    assert not origin_allowed("null", hosts)
    assert not origin_allowed("", hosts)
    assert origin_allowed("http://LOCALHOST:8000", hosts)
    assert origin_allowed("http://nas.local", ["NAS.local"])           # hôtes de la config comparés sans casse
    assert not origin_allowed("http://localhost@evil.example", hosts)


def test_foreign_origin_is_refused_before_accept(dry):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
            pass
        assert e.value.code == 1008
        assert app.state.active_sessions == 0
        with client.websocket_connect("/ws", headers={"origin": "http://localhost:8000"}) as ws:
            assert json.loads(ws.receive_text())["type"] == "ready"


# ---------- A3 : session unique ----------
def test_second_connection_is_refused_and_cannot_reset_the_vad(dry, monkeypatch):
    with TestClient(app) as client:
        app.state.settings.cfg["server"]["busy_grace_s"] = 0.1
        resets = []
        monkeypatch.setattr(app.state.vad, "reset", lambda: resets.append(1))
        with client.websocket_connect("/ws") as first:
            assert json.loads(first.receive_text())["type"] == "ready"
            assert resets == [1]
            with client.websocket_connect("/ws") as second:
                msg = json.loads(second.receive_text())
                assert msg["type"] == "error" and "déjà en cours" in msg["message"]
                with pytest.raises(WebSocketDisconnect) as e:
                    second.receive_text()
                assert e.value.code == 1013
            assert resets == [1]                      # la 2e connexion n'a pas touché au VAD de la 1re
            _text_turn_works(first)                   # et la 1re session fonctionne toujours
        app.state.settings.cfg["server"]["busy_grace_s"] = 2.0
        with client.websocket_connect("/ws") as third:  # la place est libérée à la déconnexion
            assert json.loads(third.receive_text())["type"] == "ready"
        assert app.state.active_sessions == 0


# ---------- A4 : messages malformés ----------
@pytest.mark.parametrize("raw", [
    "pas du json", "[1, 2]", "null", "42", '"texte"', "{}", '{"type": 5}', '{"type": "inconnu"}',
    '{"type": "text"}', '{"type": "text", "text": 123}', '{"type": "text", "text": null}',
    '{"type": "interrupted", "played_s": "abc"}', '{"type": "interrupted", "played_s": null}',
    '{"type": "interrupted", "played_s": NaN}', '{"type": "interrupted", "played_s": -3}',
    '{"type": "interrupted", "played_s": true}', '{"type": "interrupted", "played_s": [1]}',
])
def test_malformed_messages_do_not_kill_the_session(dry, raw):
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.receive_text()
        ws.send_text(raw)
        _text_turn_works(ws)


def test_too_long_text_is_rejected_with_a_clear_error(dry):
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.receive_text()
        limit = app.state.settings.cfg["server"]["max_text_chars"]
        ws.send_text(json.dumps({"type": "text", "text": "a" * (limit + 1)}))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error" and str(limit) in msg["hint"]
        ws.send_text(json.dumps({"type": "text", "text": "a" * limit}))   # exactement la limite : accepté
        assert "agent_chunk" in _drain_turn(ws)


def test_valid_numeric_interrupted_is_still_accepted(dry):
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "interrupted", "played_s": 1.5}))
        ws.send_text(json.dumps({"type": "interrupted", "played_s": 2}))
        _text_turn_works(ws)


# ---------- A1 : keepalive ----------
class _Probe:
    def __init__(self, on_event=None):
        self.calls, self.emit = 0, on_event

    async def warmup(self) -> None:
        self.calls += 1
        if self.emit:
            self.emit("usage", stage="llm", provider="probe", cost_usd=0.001)
            self.emit("llm_done", provider="probe")   # événement non coût : ne doit rien laisser


async def _run_keepalive(is_active, ledger_path, ticks=4):
    ledger = CostLedger(path=str(ledger_path))
    emit = usage_recorder(ledger)
    probes = [_Probe(emit) for _ in range(3)]
    task = asyncio.create_task(_keepalive(*probes, 0.01, is_active))
    await asyncio.sleep(0.01 * ticks + 0.03)
    task.cancel()
    return probes


def test_keepalive_makes_no_call_without_session(tmp_path):
    probes = asyncio.run(_run_keepalive(lambda: False, tmp_path / "l.jsonl"))
    assert [p.calls for p in probes] == [0, 0, 0]
    assert not (tmp_path / "l.jsonl").exists()


def test_keepalive_calls_are_counted_in_the_ledger_when_a_session_is_open(tmp_path):
    probes = asyncio.run(_run_keepalive(lambda: True, tmp_path / "l.jsonl"))
    calls = probes[0].calls
    assert calls >= 2 and all(p.calls == calls for p in probes)
    lines = (tmp_path / "l.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 * calls                      # une ligne de ledger par appel
    assert {json.loads(x)["provider"] for x in lines} == {"probe"}


def test_keepalive_follows_session_activity(tmp_path):
    state = {"on": False}

    async def go():
        ledger = CostLedger(path=str(tmp_path / "l.jsonl"))
        p = _Probe(usage_recorder(ledger))
        task = asyncio.create_task(_keepalive(p, p, p, 0.01, lambda: state["on"]))
        await asyncio.sleep(0.05)
        before = p.calls
        state["on"] = True
        await asyncio.sleep(0.05)
        task.cancel()
        return before, p.calls
    before, after = asyncio.run(go())
    assert before == 0 and after > 0


def test_app_module_exposes_active_session_counter(dry):
    with TestClient(app):
        assert app_mod.app.state.active_sessions == 0


def test_slot_is_released_when_session_setup_fails(dry, monkeypatch):
    """Défaut relevé par l'audit : si la création du Tracer lève (disque plein), la place ne doit pas rester prise."""
    with TestClient(app) as client:
        app.state.settings.cfg["server"]["busy_grace_s"] = 0.1
        monkeypatch.setattr(app_mod, "Tracer", lambda *a, **k: (_ for _ in ()).throw(OSError("disque plein")))
        with pytest.raises(Exception), client.websocket_connect("/ws") as ws:  # noqa: B017
            ws.receive_text()
        assert app.state.active_sessions == 0
        monkeypatch.undo()
        with client.websocket_connect("/ws") as ws:
            assert json.loads(ws.receive_text())["type"] == "ready"


def test_slot_is_released_when_cleanup_fails(dry, monkeypatch):
    from src.orchestrator.session import ConversationSession

    async def boom(self):
        raise RuntimeError("nettoyage en échec")
    with TestClient(app) as client:
        monkeypatch.setattr(ConversationSession, "close", boom)
        with pytest.raises(RuntimeError), client.websocket_connect("/ws") as ws:   # l'erreur du serveur remonte au client de test
            ws.receive_text()
        import time
        for _ in range(40):
            if app.state.active_sessions == 0:
                break
            time.sleep(0.05)
        assert app.state.active_sessions == 0


def test_reload_during_grace_period_is_accepted(dry):
    """Rechargement de page : la nouvelle connexion arrive avant que l'ancienne soit fermée côté serveur ; elle attend."""
    with TestClient(app) as client:
        app.state.settings.cfg["server"]["busy_grace_s"] = 2.0
        with client.websocket_connect("/ws") as first:
            first.receive_text()
            with client.websocket_connect("/ws") as second:
                first.close()
                assert json.loads(second.receive_text())["type"] == "ready"

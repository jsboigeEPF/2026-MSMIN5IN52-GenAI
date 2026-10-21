import json

from fastapi.testclient import TestClient

from src.transport.app import app


def test_text_turn_over_websocket_with_fakes(monkeypatch):
    monkeypatch.setenv("VOICE_DRY_RUN", "1")
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready" and ready["dry_run"] is True
        ws.send_text(json.dumps({"type": "text", "text": "Bonjour"}))
        audio_bytes, seen = 0, []
        while True:
            msg = ws.receive()
            if msg.get("bytes") is not None:
                audio_bytes += len(msg["bytes"])
                continue
            m = json.loads(msg["text"])
            seen.append(m["type"])
            if m["type"] == "audio_end":
                break
        assert audio_bytes > 0
        assert {"transcript", "agent_chunk", "metrics"} <= set(seen)
        ws.send_text(json.dumps({"type": "playback_ended"}))
        state = json.loads(ws.receive_text())
        assert state == {"type": "state", "state": "listening"}

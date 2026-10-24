"""Serveur FastAPI : page web + WebSocket /ws (micro 16 kHz PCM16 en entrée, audio 24 kHz PCM16 en sortie).

Lancer : uvicorn src.transport.app:app --port 8000   (VOICE_DRY_RUN=1 pour des faux fournisseurs, sans réseau)
Protocole : binaire client->serveur = PCM16 16 kHz ; serveur->client = PCM16 24 kHz ; texte = JSON (voir session.py).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import build_system_prompt, load_settings
from src.factory import build_providers, make_client
from src.metrics.cost import CostLedger
from src.metrics.recorder import Recorder
from src.metrics.tracer import Tracer
from src.orchestrator.session import ConversationSession
from src.tts.fillers import FillerBank, load_fillers
from src.tts.voices import pick_voice
from src.vad.endpointer import Endpointer, VadConfig
from src.vad.silero import SileroVAD

STATIC = Path(__file__).parent / "static"


async def _keepalive(stt, llm, tts, every_s: float) -> None:
    """Appels factices périodiques : le serveur Whisper se refroidit après quelques minutes (2,9 s observés au 1er appel)
    et les connexions inactives expirent. Coût négligeable (1 jeton LLM, 4 caractères TTS, 1 s de silence)."""
    while True:
        await asyncio.sleep(every_s)
        await asyncio.gather(stt.warmup(), llm.warmup(), tts.warmup(), return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = load_settings()
    dry = os.environ.get("VOICE_DRY_RUN") == "1"
    app.state.settings, app.state.dry = s, dry
    app.state.client = None if dry else make_client()
    app.state.ledger = None if dry else CostLedger(budget_usd=s.budget_usd)
    app.state.vad = SileroVAD()
    keepalive = None
    if not dry:  # préchauffage : connexions TLS ouvertes, serveur Whisper « réveillé »
        stt, llm, tts = build_providers(s, pick_voice(s.tts_voices), app.state.client, lambda *a, **k: None, False)
        await asyncio.gather(stt.warmup(), llm.warmup(), tts.warmup(), return_exceptions=True)
        keepalive = asyncio.create_task(_keepalive(stt, llm, tts, s.cfg.get("keepalive_s", 25)))
    app.state.fillers, app.state.search_fillers, app.state.wait_fillers = {}, {}, {}   # par voix
    fcfg = dict(s.cfg.get("filler", {}))
    if os.environ.get("VOICE_FILLER") == "0":  # campagne de mesures : comparaison avec / sans accroche
        fcfg["enabled"] = False
    if fcfg.get("enabled"):  # accroches pré-synthétisées pour chaque voix (cache disque : data/fillers/)
        speeds = s.cfg.get("tts", {}).get("speeds", {})
        for v in s.tts_voices or ["fake"]:
            tts_v = build_providers(s, v, app.state.client, lambda *a, **k: None, dry)[2]
            tts_v = getattr(tts_v, "primary", tts_v)  # voix de secours exclue : le cache est rangé par modèle/voix principaux
            tag = f"{'dry' if dry else s.tts_model.replace('/', '_')}_{v}_{speeds.get(v, 1.0)}"
            try:
                app.state.fillers[v] = await load_fillers(tts_v, fcfg["texts"], Path("data/fillers"), tag)
                if s.cfg.get("search", {}).get("enabled"):
                    app.state.search_fillers[v] = await load_fillers(tts_v, fcfg["search_texts"], Path("data/fillers"), tag)
                    app.state.wait_fillers[v] = await load_fillers(tts_v, fcfg["search_wait_texts"], Path("data/fillers"), tag)
            except Exception as e:  # noqa: BLE001  sans accroches la conversation reste utilisable
                print(f"accroches indisponibles pour {v} : {type(e).__name__}: {e}")
    yield
    if keepalive:
        keepalive.cancel()
    if app.state.client:
        await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    st = app.state
    s = st.settings
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + ("_dry" if st.dry else "") + "_ws"
    tracer = Tracer(run_id, s.cfg["metrics"]["trace_dir"])
    rec = Recorder(tracer, st.ledger)
    vcfg = VadConfig(**s.cfg["vad"])

    async def send_json(msg: dict) -> None:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))

    sc, fcfg = s.cfg["session"], s.cfg.get("filler", {})
    stc = s.cfg.get("stt", {})
    voice = pick_voice(s.tts_voices or ["fake"], random.Random())  # une voix par conversation
    stt, llm, tts = build_providers(s, voice, st.client, rec, st.dry)
    bank = FillerBank(list(st.fillers[voice])) if st.fillers.get(voice) else None
    search_bank = FillerBank(list(st.search_fillers[voice])) if st.search_fillers.get(voice) else None
    wait_bank = FillerBank(list(st.wait_fillers[voice])) if st.wait_fillers.get(voice) else None
    session = ConversationSession(
        stt=stt, llm=llm, tts=tts, vad=st.vad, endpointer=Endpointer(vcfg), rec=rec,
        system_prompt=build_system_prompt(s), send_json=send_json, send_audio=ws.send_bytes,
        fillers=bank, search_fillers=search_bank, wait_fillers=wait_bank,
        search_wait_after_s=fcfg.get("search_wait_after_s", 0.0), speculative_ms=stc.get("speculative_ms", 0),
        text_fallback_after=stc.get("fallback_to_text_after", 0), filler_min_speech_ms=fcfg.get("min_speech_ms", 1800),
        rest_min=s.cfg["tts"]["min_chunk_chars"], max_history=sc["max_history_messages"],
        barge_min_ms=sc["barge_in_min_speech_ms"], barge_prob=sc["barge_in_min_prob"], strict_after_s=sc["strict_after_s"])
    rec("session_start", voice=voice, dry_run=st.dry)
    await send_json({"type": "ready", "voice": voice, "dry_run": st.dry, "state": session.state})
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await session.on_mic(msg["bytes"])
            elif msg.get("text") is not None:
                data = json.loads(msg["text"])
                if data.get("type") == "text":
                    await session.on_text(data.get("text", ""))
                elif data.get("type") == "voice_retry":
                    await session.on_voice_retry()
                elif data.get("type") == "playback_ended":
                    await session.on_playback_ended()
                elif data.get("type") == "interrupted":
                    await session.on_interrupted(float(data.get("played_s", 0.0)))
    except WebSocketDisconnect:
        pass
    finally:
        rec("session_end")
        await session.close()
        tracer.close()
        with contextlib.suppress(Exception):
            await ws.close()

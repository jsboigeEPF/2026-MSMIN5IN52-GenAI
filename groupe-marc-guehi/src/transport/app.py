"""Serveur FastAPI : page web + WebSocket /ws (micro 16 kHz PCM16 en entrée, audio 24 kHz PCM16 en sortie).

Lancer : uvicorn src.transport.app:app --port 8000   (VOICE_DRY_RUN=1 pour des faux fournisseurs, sans réseau)
Protocole : binaire client->serveur = PCM16 16 kHz ; serveur->client = PCM16 24 kHz ; texte = JSON (voir docs/ARCHITECTURE.md).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import random
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import build_system_prompt, load_settings
from src.factory import build_providers, make_client
from src.metrics.cost import CostLedger
from src.metrics.recorder import Recorder
from src.metrics.tracer import Tracer
from src.orchestrator.session import ConversationSession
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.tts.fillers import FillerBank, load_fillers
from src.tts.voices import pick_voice
from src.vad.endpointer import Endpointer, VadConfig
from src.vad.silero import SileroVAD

STATIC = Path(__file__).parent / "static"


def usage_recorder(ledger: CostLedger | None) -> Callable:
    """Rappel `on_event` des appels hors conversation (préchauffage, keepalive) : seul le coût (`usage`) est retenu,
    il passe par le `Recorder` et le `CostLedger` comme celui d'un tour ; aucune trace n'est gardée (pas de fuite mémoire)."""
    rec = Recorder(None, ledger)

    def emit(event: str, **data) -> None:
        if event == "usage":
            rec(event, **data)
    return emit


async def _keepalive(stt, llm, tts, every_s: float, is_active: Callable[[], bool]) -> None:
    """Appels factices périodiques, seulement tant qu'une session est ouverte : le serveur Whisper se refroidit après
    quelques minutes (2,9 s observés au 1er appel) et les connexions inactives expirent. Ces appels sont payants
    (1 jeton LLM, 4 caractères TTS) : sans connexion, on ne dépense rien."""
    while True:
        await asyncio.sleep(every_s)
        if is_active():
            await asyncio.gather(stt.warmup(), llm.warmup(), tts.warmup(), return_exceptions=True)


def origin_allowed(origin: str | None, allowed_hosts: list[str]) -> bool:
    """Protection contre le détournement de WebSocket par une page tierce : le navigateur envoie toujours `Origin`.
    Sans en-tête (scripts de test, clients non navigateur), la connexion est acceptée : le risque ne vient que des navigateurs."""
    if origin is None:
        return True
    host = urlparse(origin).hostname
    return host is not None and host in {h.lower() for h in allowed_hosts}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = load_settings()
    dry = os.environ.get("VOICE_DRY_RUN") == "1"
    app.state.settings, app.state.dry = s, dry
    app.state.client = None if dry else make_client()
    app.state.ledger = None if dry else CostLedger(budget_usd=s.budget_usd)
    app.state.vad = SileroVAD()
    app.state.active_sessions = 0   # une seule conversation à la fois (le VAD est à état partagé)
    keepalive = None
    if not dry:  # préchauffage : connexions TLS ouvertes, serveur Whisper « réveillé »
        stt, llm, tts = build_providers(s, pick_voice(s.tts_voices), app.state.client, usage_recorder(app.state.ledger), False)
        await asyncio.gather(stt.warmup(), llm.warmup(), tts.warmup(), return_exceptions=True)
        keepalive = asyncio.create_task(
            _keepalive(stt, llm, tts, s.cfg.get("keepalive_s", 25), lambda: app.state.active_sessions > 0))
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


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


async def handle_json(session: ConversationSession, raw: str, send_json, server_cfg: dict) -> None:
    """Un message JSON du client. Tout ce qui est malformé (JSON invalide, type inconnu, champ de mauvais type, texte trop long)
    est ignoré, ou rejeté avec un message `error` pour le texte trop long : la session continue."""
    max_text_chars = server_cfg.get("max_text_chars", 2000)
    try:
        data = json.loads(raw)
    except ValueError:
        session.deps.rec("ws_bad_message", reason="json")
        return
    kind = data.get("type") if isinstance(data, dict) else None
    if kind == "text":
        text = data.get("text")
        if not isinstance(text, str):
            session.deps.rec("ws_bad_message", reason="text_type")
        elif len(text) > max_text_chars:
            session.deps.rec("ws_bad_message", reason="text_too_long", chars=len(text))
            await send_json({"type": "error", "message": "Message trop long", "hint": f"{max_text_chars} caractères au plus"})
        else:
            await session.on_text(text)
    elif kind == "mute":
        await session.on_mute(bool(data.get("muted")))
    elif kind == "voice_retry":
        await session.on_voice_retry()
    elif kind == "playback_ended":
        await session.on_playback_ended()
    elif kind == "playback_started":
        lead = data.get("in_ms")
        if _is_number(lead) and 0 <= lead <= server_cfg.get("max_playback_lead_ms", 10000):
            session.on_playback_started(float(lead))
        else:
            session.deps.rec("ws_bad_message", reason="in_ms")
    elif kind == "interrupted":
        played = data.get("played_s", 0.0)
        if _is_number(played) and played >= 0:
            await session.on_interrupted(float(played))
        else:
            session.deps.rec("ws_bad_message", reason="played_s")
    else:
        session.deps.rec("ws_bad_message", reason="unknown_type")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    st = app.state
    s = st.settings
    server_cfg = s.cfg.get("server", {})
    if not origin_allowed(ws.headers.get("origin"), server_cfg.get("allowed_origin_hosts", ["localhost", "127.0.0.1", "::1"])):
        await ws.close(code=1008)  # fermeture avant `accept` : le navigateur voit un refus
        return
    await ws.accept()
    waited = 0.0
    while st.active_sessions > 0 and waited < server_cfg.get("busy_grace_s", 2.0):  # rechargement de page : l'ancienne session se ferme
        await asyncio.sleep(0.05)
        waited += 0.05
    if st.active_sessions > 0:  # une seule conversation à la fois : un 2e client casserait l'état du VAD partagé
        await ws.send_text(json.dumps({"type": "error", "message": "Une conversation est déjà en cours",
                                       "hint": "ferme l'autre onglet puis réessaie"}, ensure_ascii=False))
        await ws.close(code=1013)
        return
    st.active_sessions += 1   # aucun `await` entre le test et l'incrément ; le `finally` ci-dessous libère toujours la place
    session, tracer = None, None
    try:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + ("_dry" if st.dry else "") + "_ws"
        tracer = Tracer(run_id, s.cfg["metrics"]["trace_dir"])
        rec = Recorder(tracer, st.ledger)
        vcfg = VadConfig(**s.cfg["vad"])

        async def send_json(msg: dict) -> None:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))

        voice = pick_voice(s.tts_voices or ["fake"], random.Random())  # une voix par conversation
        stt, llm, tts = build_providers(s, voice, st.client, rec, st.dry)

        def bank(banks: dict) -> FillerBank | None:
            return FillerBank(list(banks[voice])) if banks.get(voice) else None

        deps = SessionDeps(
            stt=stt, llm=llm, tts=tts, vad=st.vad, endpointer=Endpointer(vcfg), rec=rec,
            system_prompt=build_system_prompt(s), send_json=send_json, send_audio=ws.send_bytes,
            fillers=bank(st.fillers), search_fillers=bank(st.search_fillers), wait_fillers=bank(st.wait_fillers))
        session = ConversationSession(deps, SessionConfig.from_dict(s.cfg))
        rec("session_start", voice=voice, dry_run=st.dry)
        await send_json({"type": "ready", "voice": voice, "dry_run": st.dry, "state": session.state.value})
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await session.on_mic(msg["bytes"])
            elif msg.get("text") is not None:
                await handle_json(session, msg["text"], send_json, server_cfg)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            try:
                if session:
                    rec("session_end")
                    await session.close()
            finally:
                if tracer:
                    tracer.close()
                with contextlib.suppress(Exception):
                    await ws.close()
        finally:  # libéré même si le nettoyage est interrompu (annulation de la tâche par le serveur)
            st.active_sessions -= 1

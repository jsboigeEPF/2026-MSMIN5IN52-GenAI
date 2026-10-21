"""Démo 2 : préchauffage, Whisper (bruit, durées), conversation complète Whisper -> DeepSeek -> Qwen TTS.

Usage : python scripts/demo_conversation.py  (sortie : data/demo2/report.html + conversation.wav)
Les latences sont mesurées en direct ; le délai de fin de parole du VAD (ENDPOINT_MS) est simulé.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import os
import re
import statistics
import time
import wave
from pathlib import Path

import httpx
import jiwer
import numpy as np
from dotenv import load_dotenv

load_dotenv()
OUT = Path("data/demo2")
OUT.mkdir(parents=True, exist_ok=True)

OR = os.environ["OPENROUTER_BASE_URL"].rstrip("/")
OR_HDR = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
SCHOOL = os.environ["STT_BASE_URL"].rstrip("/")
SCHOOL_HDR = {"Authorization": f"Bearer {os.environ['STT_API_KEY']}"}
LLM = "deepseek/deepseek-v4.1-flash"
LLM_FALLBACK = os.environ["LLM_MODEL"]
TTS = os.environ["TTS_MODEL"]
AGENT_VOICE, USER_VOICE = "longanhuan_v3.6", "loongjohn"
RATE = 24000
ENDPOINT_MS = 600  # silence VAD avant de considérer la fin de tour (configs/base.yaml)
MIN_CHUNK = 20
SYSTEM = ("Tu es un assistant vocal. Réponds en français oral, 1 à 3 phrases courtes, sans markdown. "
          "Commence par une toute première phrase très brève.")

TURNS = [
    "Bonjour, tu m'entends bien ?",
    "J'ai un exposé demain sur les assistants vocaux, par quoi je commence ?",
    "D'accord. Et pourquoi la latence est-elle si importante ?",
    "Qu'est-ce qui se passe quand je coupe la parole à l'assistant ?",
    "Merci, c'est très clair. Bonne journée !",
]
WHISPER_TEXTS = {
    "courte (~2 s)": "Allume la lumière du salon, s'il te plaît.",
    "moyenne (~6 s)": "Bonjour, je voudrais connaître la météo à Lyon demain, et savoir si je dois prendre un parapluie.",
    "longue (~15 s)": ("Ce matin, j'ai pris le train de huit heures vingt pour Paris, mais il avait quarante minutes "
                       "de retard, donc j'ai raté ma correspondance et j'ai dû appeler le client pour décaler la réunion."),
}


def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def add_noise(pcm: bytes, snr_db: float, seed: int = 0) -> bytes:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    noise = np.random.default_rng(seed).standard_normal(len(x))
    noise *= np.sqrt(np.mean(x ** 2) / (10 ** (snr_db / 10))) / np.sqrt(np.mean(noise ** 2))
    return np.clip(x + noise, -32768, 32767).astype(np.int16).tobytes()


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def wer(ref: str, hyp: str) -> float:
    return jiwer.wer(norm(ref), norm(hyp))


# ---------- briques ----------
RETRIES = {"tts": 0}  # relances 429 cumulées (visibles dans les rapports)


async def tts(c: httpx.AsyncClient, text: str, voice: str = AGENT_VOICE, t_ref: float | None = None) -> dict:
    t_start = time.perf_counter()
    for attempt in range(4):
        t0 = time.perf_counter()
        pcm, first = b"", None
        async with c.stream("POST", f"{OR}/audio/speech", headers=OR_HDR, timeout=60, json={
                "model": TTS, "input": text, "voice": voice, "response_format": "pcm"}) as r:
            if r.status_code == 429 and attempt < 3:
                await r.aread()
                RETRIES["tts"] += 1
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise RuntimeError(f"TTS HTTP {r.status_code} {(await r.aread())[:150]!r}")
            async for ch in r.aiter_bytes():
                first = first or time.perf_counter()
                pcm += ch
        break
    end = time.perf_counter()
    ref = t_ref or t_start
    return {"pcm": pcm, "dur": len(pcm) / (2 * RATE), "ttfb_ms": (first - t_start) * 1000, "total_ms": (end - t_start) * 1000,
            "first_abs": (first - ref) * 1000, "text": text, "retries": attempt}


async def stt_school(c: httpx.AsyncClient, wav: bytes) -> tuple[str, float]:
    t0 = time.perf_counter()
    r = await c.post(f"{SCHOOL}/audio/transcriptions", headers=SCHOOL_HDR, timeout=120,
                     data={"model": os.environ["STT_MODEL"], "language": "fr"}, files={"file": ("a.wav", wav, "audio/wav")})
    r.raise_for_status()
    return r.json()["text"].strip(), (time.perf_counter() - t0) * 1000


async def stt_or(c: httpx.AsyncClient, wav: bytes, model: str = "qwen/qwen3-asr-1.7b") -> tuple[str, float]:
    t0 = time.perf_counter()
    r = await c.post(f"{OR}/audio/transcriptions", headers=OR_HDR, timeout=120,
                     data={"model": model, "language": "fr"}, files={"file": ("a.wav", wav, "audio/wav")})
    r.raise_for_status()
    return r.json()["text"].strip(), (time.perf_counter() - t0) * 1000


def split_sentences(buf: str, final: bool = False) -> tuple[list[str], str]:
    out = []
    while True:
        m = re.search(r"[.!?…]+[\s\n]|\n", buf)
        if not m or m.end() < MIN_CHUNK:
            # phrase trop courte : on attend la suivante (sauf si c'est la fin)
            m2 = re.search(r"[.!?…]+[\s\n]|\n", buf[MIN_CHUNK:])
            if not m2:
                break
            end = MIN_CHUNK + m2.end()
        else:
            end = m.end()
        out.append(buf[:end].strip())
        buf = buf[end:]
    if final and buf.strip():
        out.append(buf.strip())
        buf = ""
    return out, buf


async def llm_turn(c: httpx.AsyncClient, model: str, messages: list[dict], t_ref: float) -> dict:
    """Flux LLM ; chaque phrase complète lance aussitôt une tâche TTS (chevauchement)."""
    t0 = time.perf_counter()
    first = None
    text, buf = "", ""
    tasks: list[asyncio.Task] = []
    body = {"model": model, "stream": True, "max_tokens": 250, "temperature": 0.6, "messages": messages,
            "reasoning": {"enabled": False}, "provider": {"sort": "latency"}}
    async with c.stream("POST", f"{OR}/chat/completions", headers=OR_HDR, json=body, timeout=60) as r:
        if r.status_code != 200:
            raise RuntimeError(f"LLM HTTP {r.status_code} {(await r.aread())[:200]!r}")
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            ev = json.loads(line[6:])
            if "error" in ev:
                raise RuntimeError(f"LLM stream error {ev['error'].get('code')} {ev['error'].get('message')}")
            for ch in ev.get("choices", []):
                d = ch.get("delta", {}).get("content")
                if d:
                    first = first or time.perf_counter()
                    text += d
                    buf += d
                    sents, buf = split_sentences(buf)
                    for s in sents:
                        tasks.append(asyncio.create_task(tts(c, s, AGENT_VOICE, t_ref)))
    sents, buf = split_sentences(buf, final=True)
    for s in sents:
        tasks.append(asyncio.create_task(tts(c, s, AGENT_VOICE, t_ref)))
    end = time.perf_counter()
    return {"model": model, "text": text.strip(), "ttft_ms": ((first or end) - t0) * 1000,
            "llm_total_ms": (end - t0) * 1000, "tts_tasks": tasks}


# ---------- sections ----------
async def warmup() -> str:
    """Compare appel à froid (nouvelle connexion) et appel après préchauffage (connexion réutilisée)."""
    silent = wav_bytes(b"\x00\x00" * 16000)
    rows = []
    async with httpx.AsyncClient() as c:
        async def probe(name, fn):
            a = time.perf_counter(); await fn(); t1 = (time.perf_counter() - a) * 1000
            a = time.perf_counter(); await fn(); t2 = (time.perf_counter() - a) * 1000
            rows.append((name, t1, t2))

        async def llm_ping():
            r = await c.post(f"{OR}/chat/completions", headers=OR_HDR, timeout=60, json={
                "model": LLM, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}],
                "reasoning": {"enabled": False}})
            r.raise_for_status()
        await asyncio.gather(probe("STT Whisper école", lambda: stt_school(c, silent)),
                             probe("LLM " + LLM, llm_ping),
                             probe("TTS Qwen", lambda: tts(c, "Oui.")))
    body = "".join(f"<tr><td>{n}</td><td>{a:.0f} ms</td><td>{b:.0f} ms</td></tr>" for n, a, b in rows)
    return ("<h2>0. Préchauffage</h2><p>Premier appel d'une connexion neuve (TLS inclus) puis second appel sur la connexion "
            "réutilisée. Le serveur Whisper peut en plus être « froid » après inactivité (35 s observés une fois).</p>"
            f"<table><tr><th>Service</th><th>1er appel</th><th>2e appel (chaud)</th></tr>{body}</table>")


async def whisper_demo(c: httpx.AsyncClient) -> str:
    rows = []
    for label, ref in WHISPER_TEXTS.items():
        u = await tts(c, ref, USER_VOICE)
        for snr in (None, 10, 0):
            pcm = u["pcm"] if snr is None else add_noise(u["pcm"], snr)
            wav = wav_bytes(pcm)
            if snr in (None, 0):
                tag = f"w_{label[:3].strip()}_{snr}.wav".replace(" ", "")
                (OUT / tag).write_bytes(wav)
            else:
                tag = None
            res = {}
            for name, fn in (("Whisper école", stt_school), ("Qwen3 ASR 1.7B", stt_or)):
                ts, last = [], ""
                for _ in range(3):
                    try:
                        last, ms = await fn(c, wav)
                        ts.append(ms)
                    except Exception as e:
                        last = f"ERREUR {type(e).__name__}"
                res[name] = (statistics.median(ts) if ts else None, wer(ref, last) if ts else None, last)
            rows.append((label, u["dur"], "propre" if snr is None else f"{snr} dB", res, tag))
    body = ""
    for label, dur, cond, res, tag in rows:
        w, q = res["Whisper école"], res["Qwen3 ASR 1.7B"]
        f = lambda t: ("—" if t[0] is None else f"{t[0]:.0f} ms | {t[1]:.0%}")
        body += (f"<tr><td>{label}<br><small>{dur:.1f} s</small></td><td>{cond}</td>"
                 f"<td>{f(w).split(' | ')[0]}</td><td>{f(w).split(' | ')[-1]}</td><td>{html.escape(w[2])}</td>"
                 f"<td>{f(q).split(' | ')[0]}</td><td>{f(q).split(' | ')[-1]}</td><td>"
                 f"{'<audio controls src=' + tag + '></audio>' if tag else ''}</td></tr>")
    return ("<h2>1. Whisper large-v3-turbo (école) — durée et bruit</h2><p>Médiane de 3 appels par cellule. "
            "Bruit blanc ajouté à SNR fixé. WER sur le texte de référence normalisé. Qwen3 ASR en comparaison.</p>"
            "<table><tr><th>Phrase</th><th>Bruit</th><th>Whisper : temps</th><th>WER</th><th>Transcription Whisper</th>"
            "<th>Qwen ASR : temps</th><th>WER</th><th>Audio</th></tr>" + body + "</table>")


async def conversation(c: httpx.AsyncClient) -> str:
    messages = [{"role": "system", "content": SYSTEM}]
    timeline = np.zeros(0, dtype=np.int16)
    cursor = 0  # position (échantillons) de la fin du dernier segment
    rows = []

    def place(pcm: bytes, start_s: float) -> float:
        nonlocal timeline
        arr = np.frombuffer(pcm, dtype=np.int16)
        s = int(start_s * RATE)
        if len(timeline) < s + len(arr):
            timeline = np.concatenate([timeline, np.zeros(s + len(arr) - len(timeline), dtype=np.int16)])
        timeline[s:s + len(arr)] += arr
        return (s + len(arr)) / RATE

    clock = 0.0  # secondes sur la timeline
    for i, q in enumerate(TURNS, 1):
        u = await tts(c, q, USER_VOICE)
        user_start = clock + 0.4
        user_end = place(u["pcm"], user_start)
        # --- fin de parole : on mesure à partir d'ici ---
        t_ref = time.perf_counter()
        user_text, stt_ms = await stt_school(c, wav_bytes(u["pcm"]))
        messages.append({"role": "user", "content": user_text})
        model = LLM
        try:
            r = await llm_turn(c, model, messages, t_ref)
        except Exception:
            model = LLM_FALLBACK
            r = await llm_turn(c, model, messages, t_ref)
        segs = await asyncio.gather(*r["tts_tasks"])
        messages.append({"role": "assistant", "content": r["text"]})
        first = segs[0]
        # latence perçue = fin de parole -> premier son de l'agent (endpointing simulé + mesuré)
        perceived = ENDPOINT_MS + first["first_abs"]
        play_end, prev_end, gaps = user_end, None, 0.0
        agent_start = user_end + perceived / 1000
        t = agent_start
        for k, sg in enumerate(segs):
            ready = user_end + (ENDPOINT_MS + sg["first_abs"]) / 1000
            start = max(ready, t)
            if k > 0 and start > t + 0.05:
                gaps += start - t
            t = place(sg["pcm"], start)
        clock = t
        rows.append(dict(turn=i, q=q, heard=user_text, stt=stt_ms, ttft=r["ttft_ms"], model=model,
                         first_sent=first["text"], tts1=first["ttfb_ms"], perceived=perceived, gaps=gaps,
                         answer=r["text"], nsent=len(segs), llm_total=r["llm_total_ms"]))
    (OUT / "conversation.wav").write_bytes(wav_bytes(timeline.tobytes()))
    pv = [x["perceived"] for x in rows]
    body = "".join(
        f"<tr><td>{x['turn']}</td><td>{html.escape(x['q'])}<br><small>entendu : {html.escape(x['heard'])}</small></td>"
        f"<td>{x['stt']:.0f}</td><td>{x['ttft']:.0f}</td><td>{x['tts1']:.0f}</td><td><b>{x['perceived']:.0f} ms</b></td>"
        f"<td>{x['gaps']:.1f} s</td><td>{html.escape(x['answer'])}<br><small>{x['nsent']} phrase(s), modèle {x['model']}</small></td></tr>"
        for x in rows)
    return ("<h2>2. Conversation complète : Whisper école → DeepSeek V4.1 Flash → Qwen TTS (longanhuan_v3.6)</h2>"
            "<p>La voix utilisateur est simulée (TTS <code>loongjohn</code>) puis transcrite par Whisper. Le TTS démarre dès la 1re "
            "phrase du LLM. Dans le fichier audio, les silences entre l'utilisateur et l'agent reproduisent la latence mesurée "
            f"(incluant {ENDPOINT_MS} ms simulées de détection de fin de parole).</p>"
            f"<p><audio controls src='conversation.wav' style='width:100%'></audio></p>"
            f"<p>Latence perçue : p50 <b>{statistics.median(pv):.0f} ms</b>, min {min(pv):.0f}, max {max(pv):.0f} ({len(pv)} tours).</p>"
            "<table><tr><th>#</th><th>Utilisateur</th><th>STT ms</th><th>LLM TTFT ms</th><th>TTS 1er octet ms</th>"
            "<th>Perçue</th><th>Trous de lecture</th><th>Réponse</th></tr>" + body + "</table>")


async def main() -> None:
    sections = []
    print("0/2 préchauffage…")
    sections.append(await warmup())
    async with httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60)) as c:
        # préchauffage de la connexion partagée (STT, LLM, TTS) avant de mesurer
        silent = wav_bytes(b"\x00\x00" * 16000)
        await asyncio.gather(stt_school(c, silent), tts(c, "Oui."), c.post(
            f"{OR}/chat/completions", headers=OR_HDR, json={"model": LLM, "max_tokens": 1, "reasoning": {"enabled": False},
                                                            "messages": [{"role": "user", "content": "ok"}]}))
        print("1/2 Whisper…")
        sections.append(await whisper_demo(c))
        print("2/2 conversation…")
        sections.append(await conversation(c))
    page = ("<!doctype html><meta charset=utf-8><title>Démo 2</title><style>body{font:15px system-ui;max-width:1200px;"
            "margin:2rem auto;padding:0 1rem;color:#222}table{border-collapse:collapse;width:100%;margin:.5rem 0 2rem}"
            "td,th{border:1px solid #ccc;padding:6px 8px;vertical-align:top;text-align:left}th{background:#f3f3f3}"
            "small{color:#666}</style><h1>Démo 2 — préchauffage, Whisper, conversation</h1>" + "".join(sections))
    (OUT / "report.html").write_text(page, encoding="utf-8")
    print("OK ->", OUT / "report.html")


if __name__ == "__main__":
    asyncio.run(main())

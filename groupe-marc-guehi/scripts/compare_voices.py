"""Compare des voix TTS pour le français : latence, durée, empreinte, aller-retour Whisper (WER proxy), lecteur audio.

Le WER aller-retour ne mesure PAS l'accent : un mauvais accent peut être bien transcrit. L'écoute reste décisive.
Usage : python scripts/compare_voices.py  (sortie : data/voices/report.html)
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import io
import os
import re
import time
import wave
from pathlib import Path

import httpx
import jiwer
from dotenv import load_dotenv

load_dotenv()
OUT = Path("data/voices")
OUT.mkdir(parents=True, exist_ok=True)
OR = os.environ["OPENROUTER_BASE_URL"].rstrip("/")
OR_HDR = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
SCHOOL = os.environ["STT_BASE_URL"].rstrip("/")
SCHOOL_HDR = {"Authorization": f"Bearer {os.environ['STT_API_KEY']}"}

TEXTS = {
    "phonétique": "Les chaussettes de l'archiduchesse sont-elles sèches ? Un bon vin blanc, c'est très bien. "
                  "J'ai vu une jolie rue bleue au bout du pont.",
    "dialogue": "Oui, bien sûr, je peux t'aider pour ton exposé de demain. Commence par une question simple au public, "
                "puis explique comment fonctionne un assistant vocal. Rendez-vous à huit heures vingt.",
}
CANDIDATES = [  # (modèle, voix)
    ("qwen/qwen-audio-3.0-tts-flash", "longanhuan_v3.6"),
    ("qwen/qwen-audio-3.0-tts-flash", "loongjohn"),
    ("qwen/qwen-audio-3.0-tts-plus", "longanlingxin"),
    ("qwen/qwen-audio-3.0-tts-plus", "longanlufeng"),
    ("minimax/speech-2.8-turbo", "French_FemaleAnchor"),
    ("minimax/speech-2.8-turbo", "French_Female_News Anchor"),
    ("minimax/speech-2.8-turbo", "French_MaleNarrator"),
    ("minimax/speech-2.8-turbo", "French_Female_Speech_New"),
    ("minimax/speech-2.8-turbo", "French_Male_Speech_New"),
    ("minimax/speech-2.8-turbo", "English_FriendlyPerson"),
    ("microsoft/mai-voice-2.1-flash", "fr-FR-Soleil:MAI-Voice-2.1-Flash"),
    ("microsoft/mai-voice-2.1-flash", "fr-FR-Harper:MAI-Voice-2.1-Flash"),
    ("microsoft/mai-voice-2.1-flash", "fr-FR-Marc:MAI-Voice-2.1-Flash"),
    ("microsoft/mai-voice-2.1-flash", "fr-FR-Grant:MAI-Voice-2.1-Flash"),
    ("microsoft/mai-voice-2-flash", "fr-FR-Soleil:MAI-Voice-2"),
    ("deepgram/aura-2", "aura-2-agathe-fr"),
    ("deepgram/aura-2", "aura-2-hector-fr"),
    ("mistralai/voxtral-mini-tts-2603", "fr_marie_neutral"),
    ("google/gemini-3.8-flash-lite-tts", "Zephyr"),
    ("google/gemini-3.8-flash-tts", "Puck"),
    ("elevenlabs/eleven-flash-v2.5", "sarah"),
    ("hexgrad/kokoro-82m", "ff_siwis"),
]


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def synth(c: httpx.AsyncClient, model: str, voice: str, text: str) -> dict:
    err = ""
    for fmt in ("pcm", "mp3"):
        for attempt in range(3):
            t0 = time.perf_counter()
            first, data, ctype = None, b"", ""
            try:
                async with c.stream("POST", f"{OR}/audio/speech", headers=OR_HDR, timeout=90, json={
                        "model": model, "input": text, "voice": voice, "response_format": fmt}) as r:
                    ctype = r.headers.get("content-type", "")
                    if r.status_code != 200:
                        err = f"{fmt} HTTP {r.status_code} {(await r.aread())[:160].decode(errors='replace')}"
                        if r.status_code in (429, 502):
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        break
                    async for ch in r.aiter_bytes():
                        first = first or time.perf_counter()
                        data += ch
            except Exception as e:
                err = f"{fmt} {type(e).__name__}: {e}"
                continue
            end = time.perf_counter()
            rate = int(ctype.split("rate=")[1].split(";")[0]) if "rate=" in ctype else 24000
            if fmt == "pcm":
                audio, ext, dur = wav_bytes(data, rate), "wav", len(data) / (2 * rate)
            else:
                audio, ext, dur = data, "mp3", None
            return {"audio": audio, "ext": ext, "fmt": fmt, "dur": dur, "ttfb": (first - t0) * 1000,
                    "total": (end - t0) * 1000}
    return {"error": err}


async def whisper(c: httpx.AsyncClient, audio: bytes, ext: str) -> str:
    r = await c.post(f"{SCHOOL}/audio/transcriptions", headers=SCHOOL_HDR, timeout=120,
                     data={"model": os.environ["STT_MODEL"], "language": "fr"},
                     files={"file": (f"a.{ext}", audio, "audio/wav" if ext == "wav" else "audio/mpeg")})
    r.raise_for_status()
    return r.json()["text"].strip()


async def one(c: httpx.AsyncClient, sem: asyncio.Semaphore, idx: int, model: str, voice: str) -> dict:
    res = {"model": model, "voice": voice, "texts": {}}
    for key, text in TEXTS.items():
        async with sem:
            s = await synth(c, model, voice, text)
        if "error" in s:
            res["error"] = s["error"]
            return res
        name = f"{idx:02d}_{key}.{s['ext']}"
        (OUT / name).write_bytes(s["audio"])
        try:
            heard = await whisper(c, s["audio"], s["ext"])
            w = jiwer.wer(norm(text), norm(heard))
        except Exception as e:
            heard, w = f"ERREUR STT {type(e).__name__}", None
        res["texts"][key] = {"file": name, "ttfb": s["ttfb"], "total": s["total"], "dur": s["dur"], "fmt": s["fmt"],
                             "wer": w, "heard": heard, "md5": hashlib.md5(s["audio"]).hexdigest()[:8], "size": len(s["audio"])}
    return res


async def main() -> None:
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient() as c:
        results = await asyncio.gather(*[one(c, sem, i, m, v) for i, (m, v) in enumerate(CANDIDATES)])
    rows = ""
    for r in results:
        if "error" in r:
            rows += (f"<tr><td>{html.escape(r['model'])}</td><td>{html.escape(r['voice'])}</td>"
                     f"<td colspan=7 class=err>{html.escape(r['error'])}</td></tr>")
            continue
        t = r["texts"]
        ph, dl = t["phonétique"], t["dialogue"]
        wers = [x["wer"] for x in t.values() if x["wer"] is not None]
        rows += (f"<tr><td>{html.escape(r['model'])}</td><td>{html.escape(r['voice'])}</td><td>{ph['fmt']}</td>"
                 f"<td>{(ph['ttfb'] + dl['ttfb']) / 2:.0f}</td>"
                 f"<td>{'—' if not wers else f'{sum(wers) / len(wers):.0%}'}</td>"
                 f"<td>{ph['md5']}</td>"
                 f"<td><audio controls src='{ph['file']}'></audio></td><td><audio controls src='{dl['file']}'></audio></td>"
                 f"<td><small>{html.escape(ph['heard'])}</small></td></tr>")
    page = ("<!doctype html><meta charset=utf-8><title>Voix françaises</title><style>body{font:14px system-ui;max-width:1400px;"
            "margin:2rem auto;padding:0 1rem}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:5px 7px;"
            "vertical-align:top;text-align:left}th{background:#f3f3f3}.err{color:#b00}</style><h1>Comparaison de voix pour le français</h1>"
            "<p>TTFB moyen sur 2 textes (1 essai chacun, 3 requêtes en parallèle : indicatif). WER = aller-retour Whisper école ; il ne mesure "
            "pas l'accent. « Empreinte » identique = même audio (repli silencieux sur une voix par défaut possible).</p>"
            "<table><tr><th>Modèle</th><th>Voix</th><th>Format</th><th>TTFB ms</th><th>WER Whisper</th><th>Empreinte</th>"
            "<th>Phonétique</th><th>Dialogue</th><th>Transcription (phonétique)</th></tr>" + rows + "</table>")
    (OUT / "report.html").write_text(page, encoding="utf-8")
    for r in results:
        t = r.get("texts") or {}
        if t:
            print(f"{r['model'][:34]:34s} {r['voice'][:34]:34s} ttfb={sum(x['ttfb'] for x in t.values()) / len(t):5.0f}ms "
                  f"wer={[None if x['wer'] is None else round(x['wer'], 2) for x in t.values()]} md5={t['phonétique']['md5']} fmt={t['phonétique']['fmt']}")
        else:
            print(f"{r['model'][:34]:34s} {r['voice'][:34]:34s} ÉCHEC {r.get('error', '')[:140]}")
    print("OK ->", OUT / "report.html")


if __name__ == "__main__":
    asyncio.run(main())

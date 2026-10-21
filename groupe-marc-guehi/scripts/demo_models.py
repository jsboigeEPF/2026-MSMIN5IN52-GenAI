"""Démonstration des modèles : TTS -> STT -> LLM -> TTS, avec timings et rapport HTML.

Usage : python scripts/demo_models.py   (sortie : data/demo/report.html + audios)
Les noms de modèles/voix viennent de .env ; les voix des TTS alternatifs sont codées ici.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import os
import time
import wave
from pathlib import Path

import httpx
import jiwer
from dotenv import load_dotenv

load_dotenv()
OUT = Path("data/demo")
OUT.mkdir(parents=True, exist_ok=True)

OR = os.environ["OPENROUTER_BASE_URL"].rstrip("/")
OR_HDR = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
SCHOOL = os.environ["STT_BASE_URL"].rstrip("/")
SCHOOL_HDR = {"Authorization": f"Bearer {os.environ['STT_API_KEY']}"}

REF = "Bonjour, je voudrais connaître la météo à Lyon demain, et savoir si je dois prendre un parapluie."
TTS_CANDIDATES = [  # (modèle, voix)
    (os.environ["TTS_MODEL"], "longanhuan_v3.6"),
    (os.environ["TTS_MODEL"], "loongjohn"),
    ("elevenlabs/eleven-flash-v2.5", "sarah"),
    ("hexgrad/kokoro-82m", "ff_siwis"),
]
STT_CANDIDATES = ["school", "qwen/qwen3-asr-1.7b", "qwen/qwen3-asr-0.6b",
                  "mistralai/voxtral-small-24b-2507-stt", "meta/muse-voice-transcribe-1.0"]
LLMS = [(os.environ["LLM_MODEL"], False), (os.environ["LLM_MODEL_ALT"], False),
        (os.environ["LLM_MODEL"], True), ("z-ai/glm-5.3-flash", True)]  # (modèle, réflexion activée)
SYSTEM = os.environ.get("SYSTEM_PROMPT") or (
    "Tu es un assistant vocal. Réponds en français, en 1 à 3 phrases courtes, "
    "sans markdown, adaptées à l'oral.")


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def tts(client: httpx.AsyncClient, model: str, voice: str, text: str, tag: str) -> dict:
    """Streaming PCM ; renvoie timings + fichier. Repli mp3 si le modèle refuse pcm."""
    r: dict = {"model": model, "voice": voice, "text": text}
    for fmt in ("pcm", "mp3"):
        t0 = time.perf_counter()
        first = None
        data = b""
        try:
            async with client.stream("POST", f"{OR}/audio/speech", headers=OR_HDR, timeout=90, json={
                    "model": model, "input": text, "voice": voice, "response_format": fmt}) as resp:
                if resp.status_code != 200:
                    r["error"] = f"{fmt}: HTTP {resp.status_code} {(await resp.aread())[:200]!r}"
                    continue
                ctype = resp.headers.get("content-type", "")
                async for chunk in resp.aiter_bytes():
                    first = first or time.perf_counter()
                    data += chunk
        except Exception as e:
            r["error"] = f"{fmt}: {type(e).__name__}: {e}"
            continue
        r.pop("error", None)
        rate = 24000
        if "rate=" in ctype:
            rate = int(ctype.split("rate=")[1].split(";")[0])
        name = f"{tag}.wav" if fmt == "pcm" else f"{tag}.mp3"
        (OUT / name).write_bytes(wav_bytes(data, rate) if fmt == "pcm" else data)
        r.update(file=name, fmt=fmt, ttfb_ms=(first - t0) * 1000, total_ms=(time.perf_counter() - t0) * 1000,
                 audio_s=len(data) / (2 * rate) if fmt == "pcm" else None)
        return r
    return r


async def stt(client: httpx.AsyncClient, model: str, wav: bytes) -> dict:
    url, hdr, m = (f"{SCHOOL}/audio/transcriptions", SCHOOL_HDR, os.environ["STT_MODEL"]) if model == "school" \
        else (f"{OR}/audio/transcriptions", OR_HDR, model)
    t0 = time.perf_counter()
    try:
        resp = await client.post(url, headers=hdr, timeout=120, data={"model": m, "language": "fr"},
                                 files={"file": ("a.wav", wav, "audio/wav")})
        dt = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            return {"model": model, "error": f"HTTP {resp.status_code} {resp.text[:200]}", "ms": dt}
        text = resp.json()["text"]
        return {"model": model, "text": text, "ms": dt, "wer": jiwer.wer(
            norm(REF), norm(text))}
    except Exception as e:
        return {"model": model, "error": f"{type(e).__name__}: {e}", "ms": (time.perf_counter() - t0) * 1000}


def norm(s: str) -> str:
    import re
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


async def llm(client: httpx.AsyncClient, model: str, question: str, think: bool = False) -> dict:
    for attempt in range(4):
        r = await _llm(client, model, question, think)
        if "429" not in r.get("error", ""):
            return {**r, "think": think, "retries": attempt}
        await asyncio.sleep(3 * (attempt + 1))
    return {**r, "think": think}


async def _llm(client: httpx.AsyncClient, model: str, question: str, think: bool) -> dict:
    t0 = time.perf_counter()
    first = first_reason = None
    text = reasoning = ""
    ntok = 0
    body = {"model": model, "stream": True, "max_tokens": 1500, "temperature": 0.6, "reasoning": {"enabled": think},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]}
    try:
        async with client.stream("POST", f"{OR}/chat/completions", headers=OR_HDR, json=body, timeout=90) as resp:
            if resp.status_code != 200:
                return {"model": model, "error": f"HTTP {resp.status_code} {(await resp.aread())[:200]!r}"}
            async for line in resp.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                ev = json.loads(line[6:])
                if "error" in ev:  # erreur dans le flux (HTTP 200)
                    return {"model": model, "error": f"stream error {ev['error'].get('code')} {ev['error'].get('message')}"}
                for ch in ev.get("choices", []):
                    d = ch.get("delta", {})
                    rs = d.get("reasoning") or d.get("reasoning_content")
                    if rs:
                        first_reason = first_reason or time.perf_counter()
                        reasoning += rs
                    if d.get("content"):
                        first = first or time.perf_counter()
                        text += d["content"]
                        ntok += 1
    except Exception as e:
        return {"model": model, "error": f"{type(e).__name__}: {e}"}
    end = time.perf_counter()
    return {"model": model, "text": text.strip(), "reasoning": reasoning.strip(),
            "ttft_ms": ((first or end) - t0) * 1000, "total_ms": (end - t0) * 1000,
            "reasoning_ttft_ms": (first_reason - t0) * 1000 if first_reason else None,
            "chunks": ntok, "chunks_per_s": ntok / max(end - (first or t0), 1e-6)}


def fm(x, d=0):
    return "—" if x is None else f"{x:.{d}f}"


async def main() -> None:
    sections: list[str] = []
    async with httpx.AsyncClient() as c:
        # 1. TTS : même phrase, plusieurs modèles/voix
        print("1/4 TTS…")
        res = await asyncio.gather(*[tts(c, m, v, REF, f"tts_{i}") for i, (m, v) in enumerate(TTS_CANDIDATES)])
        rows = "".join(
            f"<tr><td>{html.escape(r['model'])}</td><td>{r['voice']}</td>"
            + (f"<td>{fm(r.get('ttfb_ms'))} ms</td><td>{fm(r.get('total_ms'))} ms</td><td>{fm(r.get('audio_s'), 1)} s</td>"
               f"<td><audio controls src='{r['file']}'></audio></td>" if "file" in r else
               f"<td colspan=4 class=err>{html.escape(r.get('error', ''))}</td>") + "</tr>" for r in res)
        sections.append("<h2>1. TTS — lecture d'une phrase en français</h2>"
                        f"<p class=ref>« {html.escape(REF)} »</p><table><tr><th>Modèle</th><th>Voix</th>"
                        f"<th>TTFB</th><th>Total</th><th>Durée audio</th><th>Écoute</th></tr>{rows}</table>")
        base = next((r for r in res if r.get("fmt") == "pcm" and "file" in r), None)

        # 2. STT : retranscrire l'audio produit (Qwen flash)
        if base:
            print("2/4 STT…")
            wav = (OUT / base["file"]).read_bytes()
            sres = []
            for m in STT_CANDIDATES:  # séquentiel : le serveur école peut être lent
                sres.append(await stt(c, m, wav))
                # 2e appel pour distinguer démarrage à froid / régime établi
                if "error" not in sres[-1]:
                    sres.append({**await stt(c, m, wav), "model": m + " (2e appel)"})
            rows = "".join(
                f"<tr><td>{html.escape(r['model'])}</td>"
                + (f"<td>{fm(r['ms'])} ms</td><td>{r['wer']:.0%}</td><td>{html.escape(r['text'])}</td>" if "text" in r else
                   f"<td>{fm(r.get('ms'))} ms</td><td colspan=2 class=err>{html.escape(r['error'])}</td>") + "</tr>"
                for r in sres)
            sections.append(f"<h2>2. STT — retranscription de l'audio de {html.escape(base['model'])}</h2>"
                            f"<table><tr><th>Modèle</th><th>Temps</th><th>WER</th><th>Transcription</th></tr>{rows}</table>")

        # 3. LLM : question -> réponse en streaming
        print("3/4 LLM…")
        question = "Je prépare une démo d'agent vocal. Pourquoi la latence est-elle critique en conversation ?"
        lres = await asyncio.gather(*[llm(c, m, question, t) for m, t in LLMS])
        rows = "".join(
            f"<tr><td>{html.escape(r['model'])}<br>réflexion : {'oui' if r.get('think') else 'non'}</td>"
            + (f"<td>{fm(r['ttft_ms'])} ms</td><td>{fm(r['total_ms'])} ms</td><td>{r['chunks_per_s']:.0f}/s</td>"
               f"<td>{fm(r['reasoning_ttft_ms'])}</td><td>{html.escape(r['text'])}"
               + (f"<details><summary>réflexion ({len(r['reasoning'])} car.)</summary>{html.escape(r['reasoning'])}</details>"
                  if r["reasoning"] else "") + "</td>" if "text" in r else
               f"<td colspan=5 class=err>{html.escape(r['error'])}</td>") + "</tr>" for r in lres)
        sections.append(f"<h2>3. LLM — streaming</h2><p class=ref>Question : « {html.escape(question)} »</p>"
                        f"<table><tr><th>Modèle</th><th>TTFT</th><th>Total</th><th>Débit</th><th>1er token réflexion</th>"
                        f"<th>Réponse</th></tr>{rows}</table>")

        # 4. Boucle complète : voix utilisateur -> STT -> LLM -> TTS
        print("4/4 Boucle complète…")
        user_q = "Peux-tu m'expliquer en deux phrases comment fonctionne l'interruption dans un assistant vocal ?"
        u = await tts(c, TTS_CANDIDATES[0][0], TTS_CANDIDATES[0][1], user_q, "loop_user")
        steps = [("Question utilisateur (TTS simulant la voix)", u)]
        if "file" in u and u["fmt"] == "pcm":
            uwav = (OUT / u["file"]).read_bytes()
            t = time.perf_counter()
            s = await stt(c, "qwen/qwen3-asr-1.7b", uwav)
            l = await llm(c, LLMS[0][0], s.get("text", user_q), False)
            # TTS lancé sur la 1re phrase complète
            first_sentence = (l.get("text", "").split(". ")[0] + ".") if l.get("text") else "Erreur."
            a = await tts(c, TTS_CANDIDATES[0][0], TTS_CANDIDATES[0][1], first_sentence, "loop_agent")
            e2e = (s.get("ms", 0) + l.get("ttft_ms", 0) + a.get("ttfb_ms", 0))
            sections.append(
                "<h2>4. Boucle complète (cascade STT → LLM → TTS)</h2>"
                f"<p>Entrée simulée : <audio controls src='{u['file']}'></audio><br><i>{html.escape(user_q)}</i></p>"
                f"<table><tr><th>Étape</th><th>Modèle</th><th>Temps</th><th>Sortie</th></tr>"
                f"<tr><td>STT</td><td>qwen3-asr-1.7b</td><td>{fm(s.get('ms'))} ms</td><td>{html.escape(s.get('text', s.get('error', '')))}</td></tr>"
                f"<tr><td>LLM (TTFT)</td><td>{html.escape(l['model'])}</td><td>{fm(l.get('ttft_ms'))} ms</td><td>{html.escape(l.get('text', l.get('error', '')))}</td></tr>"
                f"<tr><td>TTS (TTFB, 1re phrase)</td><td>{html.escape(a['model'])}</td><td>{fm(a.get('ttfb_ms'))} ms</td>"
                f"<td>{'<audio controls src=' + a['file'] + '></audio>' if 'file' in a else html.escape(a.get('error', ''))}</td></tr>"
                f"<tr class=sum><td colspan=2>Latence perçue estimée (STT + TTFT + TTFB, hors VAD et réseau navigateur)</td>"
                f"<td colspan=2><b>{e2e:.0f} ms</b></td></tr></table>")
            (OUT / "loop.json").write_text(json.dumps({"stt": s, "llm": l, "tts": a, "e2e_ms": e2e}, ensure_ascii=False, indent=1))

    page = ("<!doctype html><meta charset=utf-8><title>Démo modèles</title><style>"
            "body{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222}"
            "table{border-collapse:collapse;width:100%;margin:.5rem 0 2rem}td,th{border:1px solid #ccc;padding:6px 8px;"
            "vertical-align:top;text-align:left}th{background:#f3f3f3}.err{color:#b00}.ref{background:#f7f7f0;padding:8px}"
            ".sum{background:#eef7ee}details{margin-top:6px;color:#555}</style><h1>Démo des modèles — agent vocal</h1>"
            + "".join(sections))
    (OUT / "report.html").write_text(page, encoding="utf-8")
    print("OK ->", OUT / "report.html")


if __name__ == "__main__":
    asyncio.run(main())

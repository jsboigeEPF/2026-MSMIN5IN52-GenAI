"""Démo 3 : réduction de latence. Référence vs A (STT/TTS par morceaux) vs B (A + phrase d'accroche pré-enregistrée).

Usage : python scripts/demo_fast.py  (sortie : data/demo3/report.html + conv_*.wav ; fillers dans data/fillers/)
Les appels réseau sont réels ; la fin de parole (endpointing) et la lecture locale de l'accroche sont modélisées.
"""
from __future__ import annotations

import asyncio
import html
import json
import random
import re
import statistics
from pathlib import Path

import httpx
import numpy as np

import demo_conversation as dc
from demo_conversation import (AGENT_VOICE, LLM, LLM_FALLBACK, OR, OR_HDR, RATE, USER_VOICE, norm, stt_school, tts,
                               wav_bytes, wer)

OUT = Path("data/demo3")
OUT.mkdir(parents=True, exist_ok=True)
FILLER_DIR = Path("data/fillers")
FILLER_DIR.mkdir(parents=True, exist_ok=True)

REPEATS = 2
LOCAL_PLAY_MS = 30  # latence supposée de lecture d'un fichier local déjà décodé
TURNS = [
    "Bonjour, tu m'entends bien ?",
    "J'ai un exposé demain sur les assistants vocaux, et je ne sais pas par quoi commencer, tu peux m'aider ?",
    "D'accord, et pourquoi la latence est-elle si importante dans une conversation ?",
    "Et qu'est-ce qui se passe, concrètement, quand je coupe la parole à l'assistant en pleine phrase ?",
    "Merci, c'est très clair, je vais préparer mes slides. Bonne journée !",
]
FILLERS = ["Alors,", "Hmm, voyons.", "Voyons voir.", "Eh bien.", "Mmh, d'accord.", "Ok, je regarde.", "Bien sûr.",
           "Hum, une seconde."]
CONFIGS = [
    dict(name="Référence", endpoint=600, chunked_stt=False, first_min=20, filler=False),
    dict(name="A : par morceaux", endpoint=350, chunked_stt=True, first_min=8, filler=False),
    dict(name="B : par morceaux + accroche", endpoint=350, chunked_stt=True, first_min=8, filler=True),
]
SYSTEM = ("Tu es un assistant vocal. Réponds en français oral, 1 à 3 phrases courtes, sans markdown. "
          "Commence par une toute première phrase très brève (moins de 8 mots).")


def to16k(pcm: bytes) -> bytes:
    x = np.frombuffer(pcm, np.int16).astype(np.float64)
    n = int(len(x) * 16000 / RATE)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16).tobytes()


def split_chunks(pcm: bytes, min_chunk: float = 1.0, min_sil: float = 0.18) -> list[tuple[int, int]]:
    """Coupe l'audio aux silences internes (≥ min_sil) ; équivalent d'un VAD qui signale les pauses."""
    x = np.frombuffer(pcm, np.int16).astype(np.float64)
    f = int(0.02 * RATE)
    n = len(x) // f
    rms = np.sqrt((x[:n * f].reshape(n, f) ** 2).mean(1))
    sil = rms < 0.06 * np.percentile(rms, 95)
    cuts, i = [], 0
    while i < n:
        if sil[i]:
            j = i
            while j < n and sil[j]:
                j += 1
            if 0 < i and j < n and (j - i) * 0.02 >= min_sil:
                cuts.append(((i + j) // 2) * f)
            i = j
        else:
            i += 1
    bounds = [0]
    for c in cuts:
        if (c - bounds[-1]) / RATE >= min_chunk and (len(x) - c) / RATE >= 0.5:
            bounds.append(c)
    bounds.append(len(x))
    return list(zip(bounds[:-1], bounds[1:]))


def next_chunk(buf: str, first: bool, first_min: int, final: bool = False) -> tuple[str | None, str]:
    pat, lim = (r"[,;:.!?…]\s", first_min) if first else (r"[.!?…]+\s|\n", 20)
    for m in re.finditer(pat, buf):
        if m.end() >= lim:
            return buf[:m.end()].strip(), buf[m.end():]
    if final and buf.strip():
        return buf.strip(), ""
    return None, buf


async def llm_turn(c: httpx.AsyncClient, model: str, messages: list[dict], first_min: int) -> dict:
    """Flux LLM ; chaque morceau complet lance aussitôt le TTS. Temps relatifs au début de l'appel LLM."""
    import time
    t0 = time.perf_counter()
    first_tok, text, buf, tasks = None, "", "", []
    body = {"model": model, "stream": True, "max_tokens": 250, "temperature": 0.6, "messages": messages,
            "reasoning": {"enabled": False}, "provider": {"sort": "latency"}}

    def flush(final=False):
        nonlocal buf
        while True:
            ch, buf = next_chunk(buf, not tasks, first_min, final)
            if ch is None:
                return
            tasks.append(asyncio.create_task(tts(c, ch, AGENT_VOICE, t0)))

    async with c.stream("POST", f"{OR}/chat/completions", headers=OR_HDR, json=body, timeout=60) as r:
        if r.status_code != 200:
            raise RuntimeError(f"LLM HTTP {r.status_code} {(await r.aread())[:200]!r}")
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            ev = json.loads(line[6:])
            if "error" in ev:
                raise RuntimeError(f"LLM stream error {ev['error'].get('code')}")
            for ch in ev.get("choices", []):
                d = ch.get("delta", {}).get("content")
                if d:
                    first_tok = first_tok or time.perf_counter()
                    text += d
                    buf += d
                    flush()
    flush(final=True)
    end = time.perf_counter()
    return {"model": model, "text": text.strip(), "ttft_ms": ((first_tok or end) - t0) * 1000, "tasks": tasks}


async def get_fillers(c: httpx.AsyncClient) -> list[dict]:
    out = []
    for i, text in enumerate(FILLERS):
        p = FILLER_DIR / f"filler_{i}.wav"
        if not p.exists():
            r = await tts(c, text)
            p.write_bytes(wav_bytes(r["pcm"]))
        pcm = p.read_bytes()[44:]
        out.append({"text": text, "file": p, "pcm": pcm, "dur": len(pcm) / (2 * RATE)})
    return out


async def run_turn(c, cfg, history, pcm, filler):
    import time
    L = len(pcm) / (2 * RATE)
    ep = cfg["endpoint"] / 1000
    if cfg["chunked_stt"]:
        res = []
        for a, b in split_chunks(pcm):
            t, ms = await stt_school(c, wav_bytes(to16k(pcm[2 * a:2 * b]), 16000))
            res.append((b / RATE, t, ms))
        text = " ".join(t for _, t, _ in res)
        ready = 0.0
        for k, (e, _, ms) in enumerate(res):
            ready = max(ready, ((L + ep) if k == len(res) - 1 else e + 0.2) + ms / 1000)
        stt_after, nchunks = ready - L, len(res)
    else:
        text, ms = await stt_school(c, wav_bytes(pcm))
        stt_after, nchunks = ep + ms / 1000, 1
    history.append({"role": "user", "content": text})
    system = SYSTEM + (f" Tu viens de dire à voix haute « {filler['text']} » : n'ajoute pas de formule d'attente, "
                       "enchaîne directement avec la réponse." if filler else "")
    msgs = [{"role": "system", "content": system}] + history
    try:
        r = await llm_turn(c, LLM, msgs, cfg["first_min"])
    except Exception:
        r = await llm_turn(c, LLM_FALLBACK, msgs, cfg["first_min"])
    segs = await asyncio.gather(*r["tasks"])
    history.append({"role": "assistant", "content": (filler["text"] + " " if filler else "") + r["text"]})
    ready0 = stt_after + segs[0]["first_abs"] / 1000  # s après la fin de parole
    plays = []
    if filler:
        f_start = ep + LOCAL_PLAY_MS / 1000
        plays.append((filler["pcm"], f_start))
        first_sound, t = f_start, f_start + filler["dur"]
        gap_after_filler = max(0.0, ready0 - t)
    else:
        first_sound, t, gap_after_filler = ready0, ready0, 0.0
    content_start = None
    for sg in segs:
        start = max(stt_after + sg["first_abs"] / 1000, t)
        content_start = content_start if content_start is not None else start
        plays.append((sg["pcm"], start))
        t = start + sg["dur"]
    return dict(first_sound=first_sound * 1000, content=content_start * 1000, stt_after=stt_after * 1000,
                chunks=nchunks, heard=text, wer=wer(cfg["ref"], text), ttft=r["ttft_ms"], tts1=segs[0]["ttfb_ms"],
                ready0=ready0 * 1000, gap=gap_after_filler, answer=r["text"], plays=plays, L=L,
                filler=filler["text"] if filler else "")


def pct(v, q):
    return float(np.percentile(v, q))


async def main() -> None:
    rng = random.Random(7)
    async with httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60)) as c:
        print("préparation (fillers, audios utilisateur, préchauffage)…")
        fillers = await get_fillers(c)
        users = [(await tts(c, q, USER_VOICE))["pcm"] for q in TURNS]
        silent = wav_bytes(b"\x00\x00" * 16000)
        await asyncio.gather(stt_school(c, silent), tts(c, "Oui."), c.post(
            f"{OR}/chat/completions", headers=OR_HDR, json={"model": LLM, "max_tokens": 1, "reasoning": {"enabled": False},
                                                            "messages": [{"role": "user", "content": "ok"}]}))
        results = {cf["name"]: [] for cf in CONFIGS}
        wavs = {}
        for rep in range(REPEATS):
            hist = {cf["name"]: [] for cf in CONFIGS}
            tl = {cf["name"]: np.zeros(0, np.int16) for cf in CONFIGS}
            clock = {cf["name"]: 0.0 for cf in CONFIGS}
            last_filler = None
            for i, (q, pcm) in enumerate(zip(TURNS, users)):
                choices = [f for f in fillers if f is not last_filler]
                filler = rng.choice(choices)
                last_filler = filler
                for cf in CONFIGS:  # configs entrelacées pour partager les conditions réseau
                    cfg = {**cf, "ref": q}
                    print(f"  rép {rep + 1}/{REPEATS} tour {i + 1} {cf['name']}…")
                    r = await run_turn(c, cfg, hist[cf["name"]], pcm, filler if cf["filler"] else None)
                    r.update(turn=i + 1, q=q, rep=rep)
                    results[cf["name"]].append(r)
                    # timeline audio (rép 0 seulement)
                    if rep == 0:
                        name = cf["name"]
                        u_start = clock[name] + 0.5
                        u_end = u_start + r["L"]
                        end = u_end
                        for p_, start in [(pcm, 0.0)] + [(p, s) for p, s in r["plays"]]:
                            a = np.frombuffer(p_, np.int16)
                            s0 = int((u_start if start == 0.0 and p_ is pcm else u_end + start) * RATE)
                            if len(tl[name]) < s0 + len(a):
                                tl[name] = np.concatenate([tl[name], np.zeros(s0 + len(a) - len(tl[name]), np.int16)])
                            tl[name][s0:s0 + len(a)] += a
                            end = max(end, (s0 + len(a)) / RATE)
                        clock[name] = end
            if rep == 0:
                for name, arr in tl.items():
                    fn = "conv_" + re.sub(r"\W+", "_", name.lower()).strip("_") + ".wav"
                    (OUT / fn).write_bytes(wav_bytes(arr.tobytes()))
                    wavs[name] = fn

    # ---------- rapport ----------
    rows = ""
    for name, rs in results.items():
        fs = [r["first_sound"] for r in rs]
        ct = [r["content"] for r in rs]
        rows += (f"<tr><td><b>{name}</b></td><td>{len(rs)}</td><td><b>{pct(fs, 50):.0f}</b></td><td>{pct(fs, 95):.0f}</td>"
                 f"<td>{statistics.mean(fs):.0f}</td><td>{100 * np.mean([x < 1000 for x in fs]):.0f} %</td>"
                 f"<td>{100 * np.mean([x < 1500 for x in fs]):.0f} %</td><td><b>{pct(ct, 50):.0f}</b></td><td>{pct(ct, 95):.0f}</td>"
                 f"<td>{statistics.mean(r['stt_after'] for r in rs):.0f}</td><td>{statistics.mean(r['ready0'] - r['stt_after'] for r in rs):.0f}</td>"
                 f"<td>{statistics.mean(r['gap'] for r in rs):.2f} s</td><td>{statistics.mean(r['wer'] for r in rs):.0%}</td></tr>")
    summary = ("<h2>Comparaison (ms après la fin de parole)</h2><p>Relances TTS après erreur 429 (comprises dans les temps) : " + str(dc.RETRIES["tts"]) + ". « Premier son » = ce que l'utilisateur entend en premier "
               "(l'accroche dans B). « Début de la réponse » = quand le contenu commence réellement (après l'accroche si elle est plus "
               f"longue que l'attente). {REPEATS * len(TURNS)} tours par configuration : p95 indicatif seulement.</p>"
               "<table><tr><th>Config</th><th>n</th><th>1er son p50</th><th>p95</th><th>moy.</th><th>&lt; 1000 ms</th><th>&lt; 1500 ms</th>"
               "<th>Début réponse p50</th><th>p95</th><th>STT fini (moy.)</th><th>LLM+TTS 1er morceau (moy.)</th>"
               "<th>Silence après accroche</th><th>WER STT</th></tr>" + rows + "</table>")
    conv = "<h2>Conversations (rép. 1) — à écouter</h2><table><tr><th>Config</th><th>Audio</th></tr>" + "".join(
        f"<tr><td>{n}</td><td><audio controls src='{f}' style='width:100%'></audio></td></tr>" for n, f in wavs.items()) + "</table>"
    det = ""
    for name, rs in results.items():
        det += f"<h3>{name}</h3><table><tr><th>#</th><th>Utilisateur (entendu)</th><th>morceaux STT</th><th>STT fini</th><th>LLM TTFT</th><th>TTS 1er octet</th><th>1er son</th><th>Début réponse</th><th>Accroche</th><th>Réponse</th></tr>"
        for r in [x for x in rs if x["rep"] == 0]:
            det += (f"<tr><td>{r['turn']}</td><td>{html.escape(r['heard'])}</td><td>{r['chunks']}</td><td>{r['stt_after']:.0f}</td>"
                    f"<td>{r['ttft']:.0f}</td><td>{r['tts1']:.0f}</td><td>{r['first_sound']:.0f}</td><td>{r['content']:.0f}</td>"
                    f"<td>{html.escape(r['filler'])}</td><td>{html.escape(r['answer'])}</td></tr>")
        det += "</table>"
    fl = "<h2>Phrases d'accroche pré-enregistrées</h2><table><tr><th>Texte</th><th>Durée</th><th>Écoute</th></tr>" + "".join(
        f"<tr><td>{html.escape(f['text'])}</td><td>{f['dur']:.1f} s</td><td><audio controls src='../fillers/{f['file'].name}'></audio></td></tr>"
        for f in fillers) + "</table>"
    page = ("<!doctype html><meta charset=utf-8><title>Démo 3</title><style>body{font:15px system-ui;max-width:1300px;margin:2rem auto;"
            "padding:0 1rem;color:#222}table{border-collapse:collapse;width:100%;margin:.5rem 0 2rem}td,th{border:1px solid #ccc;"
            "padding:5px 7px;vertical-align:top;text-align:left}th{background:#f3f3f3}</style><h1>Démo 3 — réduire la latence</h1>"
            + summary + conv + fl + "<h2>Détail</h2>" + det)
    (OUT / "report.html").write_text(page, encoding="utf-8")
    (OUT / "results.json").write_text(json.dumps(
        {k: [{kk: vv for kk, vv in r.items() if kk != "plays"} for r in v] for k, v in results.items()},
        ensure_ascii=False, indent=1))
    print("OK ->", OUT / "report.html")


if __name__ == "__main__":
    asyncio.run(main())

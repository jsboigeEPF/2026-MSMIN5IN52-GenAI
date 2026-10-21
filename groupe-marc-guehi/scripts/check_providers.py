"""Teste STT (Whisper école), LLM (OpenRouter, streaming) et TTS (endpoint à confirmer).

Usage : python scripts/check_providers.py [--audio chemin.wav] [--tts-path /audio/speech]
Coût : quelques tokens et une phrase de TTS (négligeable). N'imprime jamais les clés.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI


async def check_llm() -> None:
    client = AsyncOpenAI(base_url=os.environ["OPENROUTER_BASE_URL"], api_key=os.environ["OPENROUTER_API_KEY"])
    t0 = time.perf_counter()
    first = None
    text = ""
    stream = await client.chat.completions.create(
        model=os.environ["LLM_MODEL"], stream=True, max_tokens=60,
        messages=[{"role": "user", "content": "Dis bonjour en une phrase, en français."}],
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            first = first or time.perf_counter()
            text += chunk.choices[0].delta.content
    print(f"[LLM] TTFT={(first - t0) * 1000:.0f} ms, total={(time.perf_counter() - t0) * 1000:.0f} ms : {text!r}")


async def check_stt(audio: str) -> None:
    client = AsyncOpenAI(base_url=os.environ["STT_BASE_URL"], api_key=os.environ["STT_API_KEY"])
    t0 = time.perf_counter()
    with open(audio, "rb") as f:
        r = await client.audio.transcriptions.create(model=os.environ["STT_MODEL"], file=f, language="fr")
    print(f"[STT] {(time.perf_counter() - t0) * 1000:.0f} ms : {r.text!r}")


async def check_tts(path: str) -> None:
    """Hypothèse : endpoint de type OpenAI /audio/speech. Affiche statut et début de réponse pour diagnostic."""
    url = os.environ["TTS_BASE_URL"].rstrip("/") + path
    body = {"model": os.environ["TTS_MODEL"], "input": "Bonjour, ceci est un test.",
            "voice": os.environ.get("TTS_VOICE", ""), "response_format": "pcm"}
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as c:
        async with c.stream("POST", url, json=body,
                            headers={"Authorization": f"Bearer {os.environ['TTS_API_KEY']}"}) as r:
            print(f"[TTS] POST {url} -> HTTP {r.status_code}, content-type={r.headers.get('content-type')}")
            n = 0
            async for chunk in r.aiter_bytes():
                if n == 0:
                    print(f"[TTS] TTFB={(time.perf_counter() - t0) * 1000:.0f} ms, premiers octets={chunk[:40]!r}")
                n += len(chunk)
            print(f"[TTS] {n} octets reçus")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio")
    ap.add_argument("--tts-path", default="/audio/speech")
    a = ap.parse_args()
    load_dotenv()
    for name, coro in [("LLM", check_llm()), ("STT", check_stt(a.audio) if a.audio else None),
                       ("TTS", check_tts(a.tts_path))]:
        if coro is None:
            print(f"[{name}] ignoré (pas de --audio)")
            continue
        try:
            await coro
        except Exception as e:  # diagnostic, on continue
            print(f"[{name}] ÉCHEC : {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())

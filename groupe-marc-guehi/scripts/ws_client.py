"""Micro simulé : envoie un ou plusieurs WAV au serveur WebSocket comme le ferait le navigateur, et affiche les événements.

Usage : python scripts/ws_client.py data/audio/question1.wav [data/audio/question2.wav ...] [--url ws://localhost:8765/ws]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.audio.pcm import duration_s, read_wav, resample


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--url", default="ws://localhost:8765/ws")
    a = ap.parse_args()
    async with websockets.connect(a.url, max_size=None) as ws:
        t0 = time.monotonic()
        ready = json.loads(await ws.recv())
        print(f"[{time.monotonic() - t0:5.2f}s] prêt, voix {ready['voice']}")
        audio_bytes = 0
        first_audio_at = None
        for path in a.wavs:
            pcm, rate = read_wav(path)
            pcm16 = resample(pcm, rate, 16000) + b"\x00\x00" * 16000 * 2  # 2 s de silence pour déclencher la fin de tour
            async def pump(pcm16=pcm16):
                for i in range(0, len(pcm16), 1024):
                    await ws.send(pcm16[i:i + 1024])
                    await asyncio.sleep(0.032)  # temps réel
            sender = asyncio.create_task(pump())
            speech_len = duration_s(resample(pcm, rate, 16000), 16000)
            t_speech_end = time.monotonic() + speech_len
            print(f"[{time.monotonic() - t0:5.2f}s] > envoi de {path} ({speech_len:.1f} s de parole)")
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=60)
                now = time.monotonic() - t0
                if isinstance(msg, bytes):
                    audio_bytes += len(msg)
                    if first_audio_at is None:
                        first_audio_at = time.monotonic()
                        print(f"[{now:5.2f}s] premier audio reçu : {(first_audio_at - t_speech_end) * 1000:.0f} ms après la fin de parole réelle")
                    continue
                m = json.loads(msg)
                if m["type"] == "metrics":
                    print(f"[{now:5.2f}s] metrics {json.dumps({k: (round(v) if isinstance(v, float) else v) for k, v in m.items()})}")
                elif m["type"] in ("transcript", "agent_chunk"):
                    print(f"[{now:5.2f}s] {m['type']}: {m['text']}")
                elif m["type"] in ("state", "error"):
                    print(f"[{now:5.2f}s] {m['type']}: {m.get('state') or m.get('message')}")
                if m["type"] == "audio_end":
                    print(f"[{now:5.2f}s] audio reçu : {audio_bytes / 48000:.1f} s")
                    await ws.send(json.dumps({"type": "playback_ended"}))
                if m["type"] == "state" and m["state"] == "listening" and audio_bytes:
                    break
            await sender
            audio_bytes, first_audio_at = 0, None


if __name__ == "__main__":
    asyncio.run(main())

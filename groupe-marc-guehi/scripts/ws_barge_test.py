"""Micro simulé avec interruption : envoie question 1, puis (N s après le 1er audio de l'agent) question 2 par-dessus.

Usage : python scripts/ws_barge_test.py data/audio/question1.wav data/audio/question2.wav [--delay 3.0] [--url ws://localhost:8765/ws]
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
from src.audio.pcm import read_wav, resample


async def stream_wav(ws, path: str, tail_s: float = 2.0) -> None:
    pcm, rate = read_wav(path)
    pcm16 = resample(pcm, rate, 16000) + b"\x00\x00" * int(16000 * tail_s)
    for i in range(0, len(pcm16), 1024):
        await ws.send(pcm16[i:i + 1024])
        await asyncio.sleep(0.032)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("q1")
    ap.add_argument("q2")
    ap.add_argument("--delay", type=float, default=3.0, help="secondes de lecture avant de parler par-dessus")
    ap.add_argument("--url", default="ws://localhost:8765/ws")
    a = ap.parse_args()
    t0 = time.monotonic()

    def log(msg: str) -> None:
        print(f"[{time.monotonic() - t0:5.2f}s] {msg}")

    async with websockets.connect(a.url, max_size=None) as ws:
        ready = json.loads(await ws.recv())
        log(f"prêt, voix {ready['voice']}")
        tasks = [asyncio.create_task(stream_wav(ws, a.q1))]
        play_start: float | None = None   # début simulé de la lecture côté client
        audio_s = 0.0
        ended_task: asyncio.Task | None = None
        turns = 0

        async def barge() -> None:
            await asyncio.sleep(a.delay)
            log(f">>> l'utilisateur parle par-dessus (q2) ; {time.monotonic() - play_start:.1f} s de lecture écoulées")
            tasks.append(asyncio.create_task(stream_wav(ws, a.q2)))

        async def playback_done(total_s: float) -> None:
            await asyncio.sleep(max(0.0, play_start + total_s - time.monotonic()))
            log("lecture simulée terminée -> playback_ended")
            await ws.send(json.dumps({"type": "playback_ended"}))

        while turns < 2:
            msg = await asyncio.wait_for(ws.recv(), timeout=90)
            if isinstance(msg, bytes):
                audio_s += len(msg) / 48000
                if play_start is None:
                    play_start = time.monotonic()
                    tasks.append(asyncio.create_task(barge()))
                    log("premier audio reçu (début de lecture simulée)")
                continue
            m = json.loads(msg)
            if m["type"] in ("transcript", "agent_chunk", "truncate"):
                log(f"{m['type']}: {m['text']}")
            elif m["type"] == "interrupt":
                played = time.monotonic() - play_start if play_start else 0.0
                log(f"INTERRUPT reçu : lecture stoppée à {played:.1f} s")
                if ended_task:
                    ended_task.cancel()
                await ws.send(json.dumps({"type": "interrupted", "played_s": played}))
                play_start, audio_s = None, 0.0
            elif m["type"] == "metrics":
                turns += 1
                log("metrics " + json.dumps({k: (round(v) if isinstance(v, float) else v) for k, v in m.items() if k != "type"},
                                            ensure_ascii=False))
            elif m["type"] == "audio_end":
                ended_task = asyncio.create_task(playback_done(audio_s))
                tasks.append(ended_task)
            elif m["type"] == "state":
                log(f"état: {m['state']}")
            elif m["type"] == "error":
                log(f"ERREUR {m['message']}")
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())

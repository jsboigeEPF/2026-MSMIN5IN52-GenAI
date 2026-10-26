"""WAV replay uses the same VAD and conversation stages as live input."""

import asyncio
import csv
import hashlib
import time
from pathlib import Path

import numpy as np

from voiceagent.audio.wav import read_wav
from voiceagent.domain import Frame, State
from voiceagent.evaluation.noise import generate_noise, mix_noise
from voiceagent.vad import TurnDetector
from voiceagent.workers import SerialWorker


def read_corpus(directory: Path) -> list[dict]:
    with (directory / "refs.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"file", "reference"}.issubset(rows[0]):
        raise ValueError("refs.csv doit contenir file,reference (et facultativement source)")
    for row in rows:
        path = (directory / row["file"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Un WAV doit rester dans le dossier du corpus")
        if not path.is_file():
            raise FileNotFoundError(f"Enregistrement manquant : {path}")
    return rows


async def replay(
    agent,
    directory: Path,
    noise_kind: str,
    snr: float | None,
    noise_file: Path | None = None,
    mock: bool = False,
    limit: int | None = None,
):
    rows = read_corpus(directory)
    if limit:
        rows = rows[:limit]
    vad = None
    if not mock:
        from voiceagent.backends.silero import Silero

        vad = await asyncio.to_thread(Silero)
    worker = SerialWorker("replay-vad")
    try:
        for index, row in enumerate(rows):
            agent.history.clear()
            if vad:
                vad.reset()
            clean = read_wav(directory / row["file"])
            digest = hashlib.sha256((directory / row["file"]).read_bytes()).hexdigest()
            # Noise continues over the silence required by endpoint detection.
            padding = np.zeros(int((agent.config.vad.silence_ms + 256) * 16), dtype=np.float32)
            signal = np.concatenate((clean, padding))
            rng = np.random.default_rng(agent.config.seed + index)
            if noise_kind == "real":
                if noise_file is None:
                    raise ValueError("--noise-file requis pour --noise real")
                noise = read_wav(noise_file)
            else:
                noise = generate_noise(noise_kind, len(signal), rng)
            mixed, metadata = mix_noise(signal, noise, snr, speech_samples=len(clean))
            detector = TurnDetector(agent.config.vad)
            began = False
            submitted = False
            origin = time.perf_counter_ns()
            # A case owns a turn even when VAD fails, so misses count in WER.
            agent.turn += 1
            case_turn = agent.turn
            agent.events.emit(
                "eval_reference",
                case_turn,
                **row,
                noise=noise_kind,
                **metadata,
                sha256=digest,
                seed=agent.config.seed + index,
            )
            if mock:
                agent.stt.text = row["reference"]
            for offset in range(0, len(mixed), 512):
                samples = mixed[offset : offset + 512]
                samples = np.pad(samples, (0, 512 - len(samples)))
                target_ns = origin + int((offset + 512) / 16000 * 1e9)
                await asyncio.sleep(max(0, (target_ns - time.perf_counter_ns()) / 1e9))
                frame = Frame(samples.astype(np.float32), target_ns)
                if mock:
                    probability = 0.95 if offset < len(clean) else 0.0
                else:
                    probability = await worker.run(vad.probability, frame.samples)
                for detection in detector.feed(frame, probability):
                    if detection.kind == "speech_start":
                        began = True
                        agent.set_state(State.USER_SPEAKING)
                        agent.events.emit(
                            "speech_start",
                            case_turn,
                            t_ns=detection.t_ns,
                            probability=probability,
                        )
                    elif detection.kind == "endpoint":
                        agent.submit_audio(detection.utterance)
                        submitted = True
                        break
                if submitted:
                    break
            if not submitted:
                agent.events.emit("replay_missed", case_turn, speech_started=began)
                agent.set_state(State.LISTENING)
            await agent.wait_idle()
            print(f"Replay {index + 1}/{len(rows)} : {row['file']}")
    finally:
        worker.close()

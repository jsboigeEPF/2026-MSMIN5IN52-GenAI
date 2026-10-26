"""Controlled E3/E4: labelled WAV + delayed output echo through the real VAD."""

import asyncio
import csv
import time
from pathlib import Path

import numpy as np

from voiceagent.audio.wav import read_wav
from voiceagent.domain import Frame
from voiceagent.vad import TurnDetector, rms
from voiceagent.workers import SerialWorker


class ScriptedAnswer:
    """A fixed long answer removes LLM variability from the interruption experiment."""

    async def stream(self, messages):
        sentence = "Internet relie des réseaux informatiques dans le monde entier. "
        for _ in range(100):
            yield sentence
            await asyncio.sleep(0)


async def benchmark(agent, cases: Path, delay: float, mock: bool):
    if not agent.player.audible:
        raise ValueError("Le banc d'interruption requiert une sortie audio")
    if delay < 0:
        raise ValueError("Délai d'injection négatif")
    with cases.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"file", "expected", "experiment"}.issubset(rows[0]):
        raise ValueError("CSV attendu : file,expected,experiment")
    vad = None
    if not mock:
        from voiceagent.backends.silero import Silero

        vad = await asyncio.to_thread(Silero)
    worker = SerialWorker("bench-vad")
    original_llm = agent.llm
    agent.llm = ScriptedAnswer()
    try:
        for row in rows:
            expected = row["expected"].lower() in {"1", "true", "yes"}
            samples = read_wav(cases.parent / row["file"])
            detector = TurnDetector(agent.config.vad)
            if vad:
                vad.reset()
            agent.player.reference.first_audio_ns = None
            turn = await agent.submit_text("Raconte une histoire longue.")
            agent.events.emit(
                "experiment_start",
                turn,
                experiment=row["experiment"],
                expected=expected,
                file=row["file"],
                scripted_llm=True,
            )
            async with asyncio.timeout(30):
                while agent.player.reference.first_audio_ns is None:
                    if not agent.busy:
                        raise RuntimeError("Aucun audio disponible pour le banc d'interruption")
                    await asyncio.sleep(0.005)
            origin = agent.player.reference.first_audio_ns
            injection_frame = round(delay / 0.032)
            count = injection_frame + int(np.ceil(len(samples) / 512)) + 20
            detected = False
            stop_ms = None
            for index in range(count):
                end_ns = origin + (index + 1) * 32_000_000
                await asyncio.sleep(max(0, (end_ns - time.perf_counter_ns()) / 1e9))
                offset = (index - injection_frame) * 512
                clean = np.zeros(512, dtype=np.float32)
                if 0 <= offset < len(samples):
                    available = samples[offset : offset + 512]
                    clean[: len(available)] = available
                echo = agent.player.reference.sample(end_ns)
                mixed = np.clip(clean + echo, -1, 1)
                if mock:
                    probability = 0.95 if rms(clean) > 0.02 else 0.0
                else:
                    probability = await worker.run(vad.probability, mixed)
                for detection in detector.feed(
                    Frame(mixed, end_ns),
                    probability,
                    busy=agent.busy,
                    reference_rms=rms(echo) * 10 ** (10 / 20),
                    half_duplex=agent.config.audio.half_duplex,
                ):
                    if detection.kind == "barge_start":
                        # Detecting echo before the labelled injection is always false.
                        before_injection = index < injection_frame
                        stop_ms = await agent.interrupt(
                            reason="benchmark", probability=probability
                        )
                        detected = True
                        outcome = "false" if before_injection or not expected else "handled"
                        break
                if detected:
                    break
            if not detected:
                outcome = "missed" if expected else "rejected"
            if outcome == "handled" and (stop_ms is None or stop_ms > 300):
                outcome = "missed"
            agent.events.emit(
                "interruption_result",
                turn,
                experiment=row["experiment"],
                expected=expected,
                outcome=outcome,
                stop_ms=stop_ms,
            )
            if agent.busy:
                await agent.interrupt(reason="benchmark_cleanup")
            agent.history.clear()
    finally:
        agent.llm = original_llm
        worker.close()

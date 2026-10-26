"""Backend construction, warming, hardware telemetry and resource ownership."""

import asyncio
import contextlib
import json
import random
import subprocess
import sys
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

import numpy as np

from voiceagent.agent import Agent
from voiceagent.audio.player import SimulatedPlayer, SoundPlayer, TextPlayer


def package_versions() -> dict:
    result = {"python": sys.version}
    for package in (
        "numpy",
        "torch",
        "kokoro",
        "faster-whisper",
        "silero-vad",
        "sounddevice",
        "httpx",
        "jiwer",
        "piper-tts",
        "ctranslate2",
        "onnxruntime",
    ):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


def memory_sample() -> dict:
    result = {"device_used_mib": None, "torch_peak_mib": None}
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout
        result["device_used_mib"] = max(float(line) for line in output.splitlines())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        result["torch_peak_mib"] = torch.cuda.max_memory_allocated() / 1024**2
    return result


async def monitor_memory(events) -> None:
    while True:
        events.emit("memory_sample", **(await asyncio.to_thread(memory_sample)))
        await asyncio.sleep(1)


def _models(config, mock: bool, need_stt: bool, audio: bool):
    if mock:
        from voiceagent.backends.mock import MockLlm, MockStt, MockTts

        return MockStt() if need_stt else None, MockLlm(), MockTts() if audio else None
    from voiceagent.backends.ollama import Ollama

    random.seed(config.seed)
    np.random.seed(config.seed)
    stt = None
    tts = None
    if need_stt:
        from voiceagent.backends.whisper import Whisper

        stt = Whisper(config.stt)
    if audio:
        import torch

        torch.manual_seed(config.seed)
        if config.tts.engine == "kokoro":
            from voiceagent.backends.kokoro import Kokoro

            tts = Kokoro(config.tts)
        else:
            from voiceagent.backends.piper import Piper

            tts = Piper(config.tts)
    return stt, Ollama(config.llm, config.seed), tts


@asynccontextmanager
async def open_agent(config, events, *, mock=False, need_stt=True, audio=True, on_token=None):
    events.emit("configuration", config=config.as_dict(), packages=package_versions())
    (events.directory / "config.json").write_text(
        json.dumps(config.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stt, llm, tts = await asyncio.to_thread(_models, config, mock, need_stt, audio)
    if not audio:
        player = TextPlayer()
    elif mock:
        player = SimulatedPlayer(events)
    else:
        player = SoundPlayer(config.audio, events)
    agent = Agent(config, events, stt, llm, tts, player, on_token)
    monitor = None
    try:
        if isinstance(player, SoundPlayer):
            await player.start()
        events.emit("warmup_start")
        if stt:
            await agent._stt_worker.run(stt.transcribe, np.zeros(16000, dtype=np.float32))
        if tts:
            await agent._tts_worker.run(tts.synthesize, "Bonjour.")
        # Fully consume this short turn to warm Ollama before measuring any user turn.
        async with contextlib.aclosing(
            llm.stream([{"role": "user", "content": "Réponds uniquement : prêt."}])
        ) as stream:
            async for _ in stream:
                pass
        events.emit("warmup_done")
        monitor = asyncio.create_task(monitor_memory(events))
        yield agent
    finally:
        if monitor:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        await agent.close()

"""Deterministic test doubles, explicitly labelled simulated in every event."""

import asyncio
import time

import numpy as np

from voiceagent.domain import AudioChunk, Samples


class MockStt:
    text = "Explique-moi ce qu'est un VAD."

    def transcribe(self, samples: Samples) -> str:
        time.sleep(0.004)
        return self.text


class MockLlm:
    async def stream(self, messages):
        answer = (
            "Un VAD détecte les passages de parole dans un signal audio. "
            "Il aide l'agent à repérer la fin de ta question. "
        )
        for word in answer.split(" "):
            await asyncio.sleep(0.003)
            yield word + " "

    async def close(self):
        return None


class MockTts:
    def synthesize(self, text: str) -> AudioChunk:
        time.sleep(0.005)
        time_axis = np.arange(2400) / 24000
        samples = (0.05 * np.sin(2 * np.pi * 220 * time_axis)).astype(np.float32)
        return AudioChunk(text, samples, 24000)

"""Exercise complete audio turns and cancellation without pretending to use hardware."""

import asyncio
import time

import numpy as np

from voiceagent.domain import Frame
from voiceagent.live import handle_frame
from voiceagent.vad import TurnDetector


async def demo(agent, turns: int = 5):
    for _ in range(turns):
        detector = TurnDetector(agent.config.vad)
        origin = time.perf_counter_ns()
        for index in range(32):
            await asyncio.sleep(0.032)
            frame = Frame(
                np.full(512, 0.15, dtype=np.float32), origin + (index + 1) * 32_000_000
            )
            probability = 0.95 if index < 14 else 0.0
            await handle_frame(agent, detector, frame, probability)
        await agent.wait_idle()
        print()
    # Cancellation during a response; unit tests cover both inference and playout.
    await agent.submit_text("Raconte-moi l'histoire d'Internet.")
    await asyncio.sleep(0.12)
    await agent.interrupt(reason="demo")
    await agent.submit_text("Stop, plutôt le Web.")
    await agent.wait_idle()

import asyncio

import numpy as np

from src.audio.noise import rms
from src.replay.mic_sim import FRAME, MicSim


async def collect(mic: MicSim, n_frames: int, on_frame=None) -> list[bytes]:
    out: list[bytes] = []
    stop = asyncio.Event()

    async def send(b: bytes) -> None:
        out.append(b)
        if on_frame:
            on_frame(len(out))
        if len(out) >= n_frames:
            stop.set()

    await asyncio.wait_for(mic.run(send, stop), 5)
    return out


async def test_speech_is_injected_in_order_and_future_resolves():
    mic = MicSim()
    fut = mic.play(np.full(FRAME * 2 + 100, 1000.0))
    frames = await collect(mic, 5)
    t_start, t_end = await fut
    assert t_end >= t_start
    levels = [rms(np.frombuffer(f, np.int16).astype(float)) for f in frames]
    assert levels[0] > 900 and levels[2] > 0 and levels[3] == 0   # 2,2 trames de parole puis silence


async def test_noise_can_be_switched_on_and_off():
    noise = np.random.default_rng(0).standard_normal(16000)
    mic = MicSim(noise, ref_rms=3000.0, snr_db=10.0)
    mic.noise_on = False
    quiet = await collect(mic, 2)
    assert all(rms(np.frombuffer(f, np.int16).astype(float)) == 0 for f in quiet)
    mic.noise_on = True
    noisy = await collect(mic, 2)
    level = rms(np.frombuffer(noisy[0], np.int16).astype(float))
    assert abs(20 * np.log10(3000 / level) - 10) < 1.5

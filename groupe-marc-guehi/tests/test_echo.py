import numpy as np

from src.audio.echo import EchoPath, echo_gain
from src.audio.noise import rms, to_pcm
from src.replay.mic_sim import FRAME, MicSim
from tests.test_mic_sim import collect


def test_gain_puts_echo_at_requested_level_relative_to_reference():
    x = np.full(1000, 500.0)
    g = echo_gain(-20.0, ref_rms=2000.0, agent_rms=500.0)
    assert abs(20 * np.log10(rms(x * g) / 2000.0) + 20) < 1e-9


def test_echo_is_delayed_and_scheduled_after_what_is_already_playing():
    p = EchoPath(delay_ms=100, gain=0.5)               # 1600 échantillons de retard
    p.feed(np.ones(1000), pos=0)
    p.feed(np.ones(1000), pos=200)                     # la lecture est séquentielle : démarre à 1000
    x = p.read(0, 4000)
    assert x[:1600].sum() == 0 and np.all(x[1600:3600] == 0.5) and x[3600:].sum() == 0


def test_stop_cuts_the_echo_after_the_path_delay_and_unqueues_the_rest():
    p = EchoPath(delay_ms=100, gain=1.0)
    p.feed(np.ones(16000), pos=0)
    p.stop(pos=1000)                                   # le HP se tait : l'écho en route (1600) arrive encore
    x = p.read(0, 8000)
    assert x[:1600].sum() == 0 and x[1600:2600].min() == 1 and x[2600:].sum() == 0
    p.feed(np.ones(100), pos=1500)                     # nouvelle lecture : repart de l'instant demandé
    assert p.read(3100, 100).sum() == 100


async def test_mic_sim_replays_agent_audio_into_the_microphone():
    mic = MicSim(echo=EchoPath(delay_ms=64, gain=1.0))
    agent = to_pcm(np.full(24000 // 10, 1000.0))       # 100 ms à 24 kHz
    mic.echo_feed(agent)
    frames = await collect(mic, 8)
    levels = [rms(np.frombuffer(f, np.int16).astype(float)) for f in frames]
    assert levels[:2] == [0.0, 0.0] and levels[2] > 900 and levels[5] > 0 and levels[7] == 0   # 2 trames de retard
    mic.echo_stop()                                    # sans effet quand plus rien ne joue
    assert FRAME == 512

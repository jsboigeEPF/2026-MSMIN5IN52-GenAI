import asyncio
import json
from dataclasses import replace
from pathlib import Path

import yaml

from src.fakes import FakeLLM, FakeSTT, FakeTTS
from src.metrics.recorder import Recorder
from src.orchestrator.session import ConversationSession
from src.orchestrator.session_config import SessionConfig, SessionDeps
from src.orchestrator.state import State
from src.tts.fillers import Filler, FillerBank
from src.vad.endpointer import Endpointer, VadConfig

FRAME = b"\x01\x00" * 512
BASE = yaml.safe_load(Path("configs/base.yaml").read_text(encoding="utf-8"))
LISTENING, SPEAKING = State.LISTENING, State.SPEAKING


class ScriptedVAD:
    def __init__(self):
        self.p = 0.0

    def reset(self):
        pass

    def prob(self, frame):
        return self.p


class SlowTTS(FakeTTS):
    """Audio plus long et plus lent que le temps réel de test : laisse le temps d'interrompre."""

    async def stream(self, text):
        async for pcm in super().stream(text):
            await asyncio.sleep(0.05)
            yield pcm


def make(fillers=None, **cfg_over):
    sent: list = []

    async def send_json(m):
        sent.append(m)

    async def send_audio(b):
        sent.append(("audio", len(b)))

    vad = ScriptedVAD()
    deps = SessionDeps(
        stt=FakeSTT("ma question assez longue"), llm=FakeLLM("Première phrase de la réponse. Seconde phrase de la réponse. "
                                                              "Troisième phrase de la réponse."),
        tts=SlowTTS(), vad=vad, endpointer=Endpointer(VadConfig(end_silence_ms=100)), rec=Recorder(),
        system_prompt="s", send_json=send_json, send_audio=send_audio, fillers=fillers)
    cfg = replace(SessionConfig.from_dict(BASE), strict_transitions=True, filler_min_speech_ms=100, barge_prob=0.75,
                  speculative_ms=0, text_fallback_after=0, search_wait_after_s=0.0, **cfg_over)
    s = ConversationSession(deps, cfg)
    return s, vad, sent


async def feed(s, vad, p, n):
    vad.p = p
    for _ in range(n):
        await s.on_mic(FRAME)
        await asyncio.sleep(0)


async def until(cond, timeout=5.0):
    t = 0.0
    while not cond():
        await asyncio.sleep(0.01)
        t += 0.01
        assert t < timeout, "condition jamais remplie"


async def user_speaks_a_turn(s, vad):
    await feed(s, vad, 0.9, 20)   # parole
    await feed(s, vad, 0.0, 10)   # silence -> fin de tour


async def test_barge_in_cancels_turn_and_truncates_history():
    s, vad, sent = make()
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state == SPEAKING and s.resp.first_audio_at is not None)
    await asyncio.sleep(0.15)                       # un peu d'audio envoyé et « joué »
    await feed(s, vad, 0.95, 14)                    # 14 x 32 ms > 300 ms de parole forte : interruption
    types = [m["type"] for m in sent if isinstance(m, dict)]
    assert "interrupt" in types and s.state == LISTENING
    assert s.tasks.turn.done()
    last = s.history[-1]
    assert last["role"] == "assistant" or last["role"] == "user"
    if last["role"] == "assistant":
        assert "[interrompu par l'utilisateur]" in last["content"]
        assert "Troisième" not in last["content"]
    assert any(isinstance(m, dict) and m["type"] == "truncate" for m in sent)


async def test_client_report_refines_truncation_and_records_ack():
    s, vad, _ = make()
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state == SPEAKING)
    await asyncio.sleep(0.2)
    await feed(s, vad, 0.95, 14)
    await s.on_interrupted(played_s=0.0)            # le client dit n'avoir rien joué
    assert any(e == "playback_stopped" for e, _, _ in s.deps.rec.events)


async def test_weak_voice_does_not_interrupt_while_agent_speaks():
    s, vad, sent = make()
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state == SPEAKING)
    await feed(s, vad, 0.6, 30)                      # 0,6 < seuil anti-écho 0,75 : ignoré
    assert not any(isinstance(m, dict) and m["type"] == "interrupt" for m in sent)


async def test_interrupted_before_transcription_keeps_the_audio_for_next_turn():
    class SlowSTT(FakeSTT):
        async def transcribe(self, pcm16, sample_rate):
            await asyncio.sleep(5)
            return "jamais"

    s, vad, _ = make()
    s.deps.stt = SlowSTT()
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state != LISTENING)
    await feed(s, vad, 0.95, 14)
    assert s.interruptions.carry is not None and len(s.interruptions.carry) > 0


async def test_filler_is_sent_first_and_stored_in_history():
    bank = FillerBank([Filler("Hmm, voyons.", b"\x01\x00" * 24000, 1.0)])
    s, vad, sent = make(bank)
    await user_speaks_a_turn(s, vad)
    await until(lambda: any(isinstance(m, dict) and m["type"] == "audio_end" for m in sent))
    chunks = [m["text"] for m in sent if isinstance(m, dict) and m["type"] == "agent_chunk"]
    assert chunks[0] == "Hmm, voyons."
    kinds = [m["type"] if isinstance(m, dict) else m[0] for m in sent]
    assert kinds.index("audio") > kinds.index("user_pending")
    assert s.history[-1]["content"].startswith("Hmm, voyons. ")
    metrics = next(m for m in sent if isinstance(m, dict) and m["type"] == "metrics")
    assert metrics["filler"] == "Hmm, voyons." and metrics["first_sound_ms"] < metrics["content_ms"] + 1
    json.dumps(metrics)


async def interrupt_then_start_second_turn(s, vad, sent):
    """Tour 1 interrompu, l'utilisateur enchaîne : le tour 2 va jusqu'au bout. Retourne le message de l'agent du tour 1."""
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state == SPEAKING and s.resp.first_audio_at is not None)
    await asyncio.sleep(0.15)
    await feed(s, vad, 0.95, 14)
    first = s.history[-1]
    assert first["role"] == "assistant" and first["content"].endswith("[interrompu par l'utilisateur]")
    await feed(s, vad, 0.95, 6)
    await feed(s, vad, 0.0, 10)                      # fin du 2e énoncé -> tour 2
    await until(lambda: any(isinstance(m, dict) and m["type"] == "audio_end" for m in sent))
    return first


async def test_late_client_report_never_rewrites_the_next_turns_answer():
    """Course : l'accusé `interrupted` du client arrive après le début (ici la fin) d'un nouveau tour. Avec un index mémorisé
    et un historique rogné à 2 messages, l'ancien code réécrivait la réponse du tour 2."""
    s, vad, sent = make(max_history=2)
    first = await interrupt_then_start_second_turn(s, vad, sent)
    answer2 = s.history[-1]
    assert answer2 is not first and s.history.messages == [s.history[0], answer2]
    before = answer2["content"]
    await s.on_interrupted(played_s=0.3)
    assert answer2["content"] == before
    assert any(isinstance(m, dict) and m["type"] == "truncate" for m in sent)


async def test_late_client_report_refines_the_interrupted_answer_in_place():
    s, vad, sent = make()
    first = await interrupt_then_start_second_turn(s, vad, sent)
    answer2 = s.history[-1]
    before = answer2["content"]
    await s.on_interrupted(played_s=0.3)
    await s.on_interrupted(played_s=0.3)             # sans interruption en attente : ignoré
    assert first in s.history.messages and answer2["content"] == before
    assert first["content"].endswith("[interrompu par l'utilisateur]") and "Troisième" not in first["content"]


async def test_client_report_after_timeout_is_ignored():
    s, vad, _ = make(interrupt_ack_timeout_s=0.0)
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state == SPEAKING)
    await asyncio.sleep(0.1)
    await feed(s, vad, 0.95, 14)
    await asyncio.sleep(0.01)
    await s.on_interrupted(played_s=0.0)
    assert not any(e == "playback_stopped" for e, _, _ in s.deps.rec.events)


async def test_barge_in_goes_through_barge_in_state_and_is_traced():
    s, vad, sent = make()
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state == SPEAKING)
    await feed(s, vad, 0.95, 14)
    changes = [(d["from"], d["to"]) for e, _, d in s.deps.rec.events if e == "state_change"]
    assert changes[-2:] == [("speaking", "barge_in"), ("barge_in", "listening")]
    states = [m["state"] for m in sent if isinstance(m, dict) and m["type"] == "state"]
    assert "barge_in" not in states and states[-1] == "listening"


async def test_muted_mic_never_interrupts_and_resumes_after_unmute():
    s, vad, sent = make()
    await user_speaks_a_turn(s, vad)
    await until(lambda: s.state == SPEAKING)
    await s.on_mute(True)
    await feed(s, vad, 0.95, 30)                     # bruit fort ambiant : ignoré
    assert not any(isinstance(m, dict) and m["type"] == "interrupt" for m in sent)
    await s.on_mute(False)
    await feed(s, vad, 0.95, 14)                     # micro rétabli : l'interruption fonctionne
    assert any(isinstance(m, dict) and m["type"] == "interrupt" for m in sent)

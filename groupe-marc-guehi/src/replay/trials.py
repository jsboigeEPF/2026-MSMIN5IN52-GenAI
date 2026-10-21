"""Essais de la campagne contre un serveur réel : latence par tour, interruption (barge-in), faux déclenchements."""
from __future__ import annotations

import asyncio
import time

import numpy as np

from src.metrics.analysis import is_prefix_of_sent, wer
from src.replay.client import ReplayClient
from src.replay.mic_sim import MicSim

TURN_TIMEOUT_S = 40.0


async def latency_turn(c: ReplayClient, mic: MicSim, utt: dict, samples: np.ndarray, turn_idx: int) -> dict:
    """Un tour : l'énoncé est joué au micro ; on mesure en temps réel la fin de parole -> premiers sons de l'agent."""
    row: dict = {"utt_id": utt["id"], "turn": turn_idx, "speech_s": round(len(samples) / 16000, 2), "voice": c.voice,
                 "failed": False}
    mark = c.mark()
    _, t_end = await mic.play(samples)
    try:
        _, metrics = await c.wait_for(lambda e: e.kind in ("metrics", "error"), after=mark, timeout=TURN_TIMEOUT_S)
    except TimeoutError:
        return {**row, "failed": True, "error": "aucun tour terminé (fin de parole jamais détectée ?)"}
    if metrics.kind == "error":
        return {**row, "failed": True, "error": metrics.data.get("message", "")[:200]}
    m = metrics.data
    audio = c.since(mark, "audio")
    after_end = [e for e in audio if e.t > t_end]   # l'audio émis avant la fin de parole est une réponse prématurée
    first = after_end[0].t if after_end else None
    # avec accroche : 1re trame audio = accroche, la suivante = début de la vraie réponse
    content = after_end[1].t if (m.get("filler") and len(after_end) > 1) else first
    transcripts = [e.data["text"] for e in c.since(mark, "transcript")]
    tr = " ".join(transcripts)
    row.update({
        "premature_start": len(after_end) < len(audio), "n_user_turns": len(transcripts),
        "filler": m.get("filler"), "transcript": tr, "wer": wer(utt["text"], tr),
        "wall_first_ms": None if first is None else (first - t_end) * 1000,
        "wall_content_arrival_ms": None if content is None else (content - t_end) * 1000,
        "srv_first_sound_ms": m.get("first_sound_ms"), "srv_content_ms": m.get("content_ms"),
        "srv_gap_ms": m.get("gap_ms"), "stt_ms": m.get("stt_ms"), "llm_ttft_ms": m.get("llm_ttft_ms"),
        "tts_ttfb_ms": m.get("tts_ttfb_ms"), "end_silence_ms": m.get("end_silence_ms"),
    })
    await c.wait_for(lambda e: e.kind == "state" and e.data.get("state") == "listening", after=mark, timeout=15)
    await asyncio.sleep(0.6)
    return row


async def barge_trial(url: str, mic: MicSim, prompt: np.ndarray, barge_utt: dict, barge: np.ndarray, prompt_text: str,
                      offset_s: float, wer_refs: list[str]) -> dict:
    """Question longue -> l'agent répond -> `offset_s` après son 1er audio, l'utilisateur parle par-dessus."""
    row: dict = {"offset_s": offset_s, "utt_id": barge_utt["id"], "valid": True}
    async with ReplayClient(url, mic, sim_playback=True) as c:
        row["voice"] = c.voice
        m0 = c.mark()
        await mic.play(prompt)
        try:
            _, first_audio = await c.wait_for(lambda e: e.kind == "audio", after=m0, timeout=TURN_TIMEOUT_S)
        except TimeoutError:
            return {**row, "valid": False, "invalid_reason": "pas de réponse à la question"}
        await asyncio.sleep(max(0.0, first_audio.t + offset_s - time.monotonic()))
        if c.state not in ("speaking", "thinking"):
            spontaneous = len(c.since(m0, "interrupt"))   # le bruit a-t-il lui-même interrompu l'agent ?
            reason = "faux positif : interruption spontanée due au bruit" if spontaneous else f"l'agent ne parlait plus (état {c.state})"
            return {**row, "valid": False, "invalid_reason": reason, "spontaneous_interrupts": spontaneous}
        m1 = c.mark()
        t_inj, t_inj_end = await mic.play(barge)
        row["t_injection_after_first_audio_s"] = round(t_inj - first_audio.t, 2)
        sent_chunks = [e.data["text"] for e in c.events[m0:m1] if e.kind == "agent_chunk"]
        try:
            i_int, ev_int = await c.wait_for(lambda e: e.kind == "interrupt", after=m1, timeout=3.0 + len(barge) / 16000)
        except TimeoutError:
            return {**row, "detect_ms": None, "interrupted": False}
        row.update({"interrupted": True, "detect_ms": (ev_int.t - t_inj) * 1000})
        try:
            i_pend, _ = await c.wait_for(lambda e: e.kind == "user_pending", after=i_int, timeout=15)
        except TimeoutError:
            i_pend = len(c.events)
        row["frames_after_interrupt"] = sum(1 for e in c.events[i_int:i_pend] if e.kind == "audio")
        trunc = next((e.data["text"] for e in c.events[i_int:] if e.kind == "truncate"), None)
        row["truncate_text"] = trunc
        row["truncate_prefix_ok"] = trunc is not None and is_prefix_of_sent(trunc.rstrip("…"), sent_chunks)
        await asyncio.sleep(max(0.0, t_inj_end + 2.0 - time.monotonic()))   # laisse finir l'énoncé et la fin de tour
        # une pause interne peut couper la phrase en plusieurs tours : on attend que les transcriptions se stabilisent
        i_tr = None
        for _ in range(5):
            trs = [i for i, e in enumerate(c.events) if i >= i_int and e.kind == "transcript"]
            if not trs:
                try:
                    await c.wait_for(lambda e: e.kind == "transcript", after=i_int, timeout=20)
                except TimeoutError:
                    break
                continue
            i_tr = trs[-1]
            try:
                await c.wait_for(lambda e: e.kind in ("metrics", "error"), after=i_tr, timeout=TURN_TIMEOUT_S)
            except TimeoutError:
                break
            if [i for i, e in enumerate(c.events) if i >= i_int and e.kind == "transcript"][-1] == i_tr:
                break
        texts = [e.data["text"] for e in c.events[i_int:] if e.kind == "transcript"]
        row["n_user_turns"] = len(texts)
        row["transcript"] = " ".join(texts) if texts else None
        row["wer"] = min(wer(r, row["transcript"]) for r in wer_refs) if texts else None  # avec ou sans la phrase précédente reprise
        row["followup_audio"] = i_tr is not None and any(e.kind == "audio" for e in c.events[i_tr:])
        row["followup_error"] = next((e.data.get("message") for e in c.events[i_int:] if e.kind == "error"), None)
    return row


async def false_positive_trial(url: str, mic: MicSim, prompt: np.ndarray) -> dict:
    """L'agent parle, personne n'interrompt (bruit seul, allumé quand l'agent commence à parler) : tout `interrupt`
    est un faux positif. Un essai s'arrête au premier faux positif (l'audio de l'agent est alors coupé)."""
    async with ReplayClient(url, mic, sim_playback=True) as c:
        m0 = c.mark()
        mic.noise_on = False   # la question est posée au calme
        await mic.play(prompt)
        try:
            _, first = await c.wait_for(lambda e: e.kind == "audio", after=m0, timeout=TURN_TIMEOUT_S)
            mic.noise_on = True
            _, ev = await c.wait_for(lambda e: e.kind in ("audio_end", "interrupt"), after=m0, timeout=TURN_TIMEOUT_S)
        except TimeoutError:
            return {"valid": False, "invalid_reason": "pas de réponse à la question", "voice": c.voice}
        if ev.kind == "interrupt":
            return {"valid": True, "voice": c.voice, "false_interrupts": 1, "first_false_after_s": round(ev.t - first.t, 2),
                    "agent_audio_s": round(c.audio_s, 1)}
        total_s = c.audio_s
        await asyncio.sleep(max(0.0, (c.play_start or time.monotonic()) + total_s - time.monotonic()) + 1.0)
        return {"valid": True, "voice": c.voice, "false_interrupts": len(c.since(m0, "interrupt")),
                "agent_audio_s": round(total_s, 1)}

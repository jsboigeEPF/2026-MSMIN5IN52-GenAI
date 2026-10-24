"""Message `metrics` envoyé au client à la fin d'un tour (définitions : docs/METRICS.md)."""
from __future__ import annotations

from src.metrics.recorder import Recorder
from src.orchestrator.response import ResponseState
from src.orchestrator.turn import TurnResult


def event_ms(rec: Recorder, event: str, start: int) -> float | None:
    """Durée `ms` du premier événement `event` enregistré depuis l'indice `start`."""
    vals = [d.get("ms") for e, _, d in rec.events[start:] if e == event and d.get("ms") is not None]
    return vals[0] if vals else None


def turn_metrics(rec: Recorder, n0: int, r: TurnResult, resp: ResponseState, end_sil: int) -> dict:
    """1er son = l'accroche ; la réponse ne démarre qu'une fois l'attente (accroches) terminée."""
    fc, lead = resp.first_content_s, resp.lead
    if fc is None:
        first_sound = content = None
    elif lead:
        first_sound, content = end_sil + lead[0] * 1000, end_sil + max(fc, lead[1]) * 1000
    else:
        first_sound = content = end_sil + fc * 1000
    return {
        "type": "metrics", "turn": rec.turn, "stt_ms": r.stt_ms, "end_silence_ms": end_sil,
        "llm_ttft_ms": event_ms(rec, "llm_first_token", n0), "tts_ttfb_ms": event_ms(rec, "tts_first_pcm", n0),
        "first_sound_ms": first_sound, "content_ms": content, "total_ms": r.total_ms,
        "filler": " ".join(resp.spoken) or None, "search_ms": event_ms(rec, "search_done", n0), "searched": resp.searched,
        "gap_ms": max(0.0, (fc - lead[1]) * 1000) if lead and fc is not None else 0.0}

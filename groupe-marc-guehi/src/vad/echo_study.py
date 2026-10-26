"""Étude hors ligne (sans réseau) de l'écho : faux barge-in, interruptions correctes et fin de tour, par gain d'écho et seuils.
Déterministe (aucun tirage aléatoire) : mêmes stimuli et mêmes réglages donnent les mêmes lignes."""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from src.audio.echo import MIC_RATE, echo_gain
from src.audio.noise import speech_rms
from src.vad.endpointer import VadConfig
from src.vad.eot_heuristic import internal_pauses, looks_incomplete, prefix_at
from src.vad.scan import EchoCase, classify_barge, clip_probs, end_of_turn_after_barge, scan
from src.vad.silero import SileroVAD


def _gain(db: float | None, ref_rms: float, agent: np.ndarray) -> float | None:
    return None if db is None else echo_gain(db, ref_rms, speech_rms(agent))


def iter_echo_study(cfg: dict, base: dict, audio: dict[str, np.ndarray], ref_rms: float, vad: SileroVAD
                    ) -> Iterator[tuple[str, dict]]:
    """Produit des lignes ("false" | "detect" | "eot", dict). cfg = campaign.yaml, base = base.yaml."""
    e, sw = cfg["echo"], cfg["echo"]["sweep"]
    vcfg, sess = VadConfig(**base["vad"]), base["session"]
    cur_sp, cur_mm = sess["barge_in_min_prob"], sess["barge_in_min_speech_ms"]
    max_ms, lead = cfg["barge"]["thresholds"]["detect_max_ms"], int(e["lead_s"] * MIC_RATE)
    grid = [(sp, mm) for sp in sw["strict_prob"] for mm in sw["min_ms"]]
    # 1) écho seul : l'agent parle, personne n'interrompt -> tout déclenchement est un faux barge-in
    for db in e["gains_db"]:
        for aid in e["false_agent_ids"]:
            agent = audio[aid]
            case = EchoCase(agent, lead, _gain(db, ref_rms, agent), e["delay_ms"])
            probs = clip_probs(vad, case.render())
            for sp, mm in grid:
                _, at = scan(probs, vcfg, strict_prob=sp, barge_ms=mm)
                yield "false", {"gain_db": db, "agent_id": aid, "strict_prob": sp, "min_ms": mm, "triggered": at is not None,
                                "t_ms": None if at is None else at - lead / MIC_RATE * 1000}
    # 2) l'utilisateur interrompt par-dessus l'écho (gain None = contrôle sans écho) + fin de tour qui suit
    for db in [None, *e["gains_db"]]:
        for aid in e["agent_ids"]:
            agent = audio[aid]
            for uid in e["user_ids"]:
                for off in e["offsets_s"]:
                    case = EchoCase(agent, lead, _gain(db, ref_rms, agent), e["delay_ms"], audio[uid], lead + int(off * MIC_RATE))
                    if lead + len(agent) + case.delay_samples < case.user_start + int(0.8 * MIC_RATE):
                        continue   # l'agent aurait fini de parler : pas d'écho sous la parole de l'utilisateur
                    probs = clip_probs(vad, case.render())
                    key = {"gain_db": db, "agent_id": aid, "user_id": uid, "offset_s": off}
                    for sp, mm in grid:
                        _, at = scan(probs, vcfg, strict_prob=sp, barge_ms=mm)
                        yield "detect", {**key, "strict_prob": sp, "min_ms": mm, "label": classify_barge(at, case, max_ms),
                                         "detect_ms": None if at is None else at - case.user_start_ms}
                    _, at = scan(probs, vcfg, strict_prob=cur_sp, barge_ms=cur_mm)
                    if classify_barge(at, case, max_ms) == "ok":
                        for es, r in end_of_turn_after_barge(vad, case, vcfg, cur_sp, at, e["agent_stop_ms"],
                                                             sw["end_silence_ms"]).items():
                            yield "eot", {**key, "end_silence_ms": es, **r}


def iter_pause_study(cfg: dict, base: dict, audio: dict[str, np.ndarray], texts: dict[str, str], vad: SileroVAD
                     ) -> Iterator[dict]:
    """Pour chaque énoncé propre : pauses internes et verdict de l'heuristique « phrase incomplète » sur le texte estimé."""
    a = cfg["adaptive"]
    vcfg, last, trailing = VadConfig(**base["vad"]), set(a["incomplete_last_words"]), tuple(a["incomplete_trailing"])
    for uid in [u["id"] for u in cfg["utterances"]] + [u["id"] for u in cfg["barge"]["utterances"]]:
        probs = clip_probs(vad, np.concatenate([np.zeros(MIC_RATE // 2), audio[uid], np.zeros(MIC_RATE)]))
        pauses, total = internal_pauses(probs, vcfg, a["min_pause_ms"])
        text = texts[uid]
        yield {"utt_id": uid, "text": text, "speech_ms": total,
               "final_incomplete": looks_incomplete(text, last, trailing, a["min_words"]),
               "pauses": [{"start_ms": p.start_ms, "dur_ms": p.dur_ms, "prefix": (pre := prefix_at(text, p.frac)),
                           "incomplete": looks_incomplete(pre, last, trailing, a["min_words"])} for p in pauses]}

"""Fonctions d'analyse de la campagne (utilisées par le notebook et les tests) : WER, percentiles, verdict de barge-in."""
from __future__ import annotations

import json
import re
from pathlib import Path

import jiwer
import numpy as np


def normalize_fr(s: str) -> str:
    """Normalisation identique pour toutes les conditions : minuscules, apostrophes/ponctuation retirées, espaces normalisés."""
    s = s.lower().replace("’", "'")
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def wer(ref: str, hyp: str) -> float:
    r, h = normalize_fr(ref), normalize_fr(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return float(jiwer.wer(r, h)) if h else 1.0


def percentiles(values, qs=(50, 95)) -> dict:
    v = np.asarray([x for x in values if x is not None and not np.isnan(x)], dtype=float)
    if len(v) == 0:
        return {f"p{q}": float("nan") for q in qs} | {"n": 0}
    return {f"p{q}": float(np.percentile(v, q)) for q in qs} | {"n": len(v)}


def read_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path: str | Path, row: dict) -> None:
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_prefix_of_sent(heard: str, sent_chunks: list[str]) -> bool:
    """Le texte conservé après interruption doit être un début de ce que l'agent a réellement envoyé (mot à mot)."""
    h = normalize_fr(heard).split()
    s = normalize_fr(" ".join(sent_chunks)).split()
    return len(h) <= len(s) and s[:len(h)] == h


def barge_verdict(t: dict, detect_max_ms: float, wer_max: float) -> dict:
    """Critères d'une interruption « correctement gérée » (docs/METRICS.md). `t` : mesures brutes d'un essai.
    Retourne chaque critère (bool) et `ok` = tous vrais."""
    detected = t.get("detect_ms") is not None
    c = {
        "detected": detected and t["detect_ms"] <= detect_max_ms,
        "stopped_clean": detected and t.get("frames_after_interrupt", 1) == 0,
        "truncated_ok": detected and t.get("truncate_text") is not None and t.get("truncate_prefix_ok", False),
        "understood": detected and t.get("wer") is not None and t["wer"] <= wer_max,
        "answered": detected and bool(t.get("followup_audio")),
    }
    return {**c, "ok": all(c.values())}

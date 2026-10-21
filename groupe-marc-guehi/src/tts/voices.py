"""Choix de la voix de l'agent : tirage au hasard au début de chaque conversation."""
from __future__ import annotations

import os
import random


def voices_from_env() -> list[str]:
    return [v.strip() for v in os.environ["TTS_VOICES"].split(",") if v.strip()]


def pick_voice(voices: list[str], rng: random.Random | None = None) -> str:
    """Une voix par conversation ; elle ne change pas en cours de conversation."""
    if not voices:
        raise ValueError("aucune voix configurée (TTS_VOICES)")
    return (rng or random).choice(voices)

"""Chargement de la configuration : .env (secrets, modèles) + configs/base.yaml (réglages)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    stt_base_url: str
    stt_api_key: str
    stt_model: str
    openrouter_base_url: str
    openrouter_api_key: str
    llm_model: str
    llm_model_alt: str
    tts_base_url: str
    tts_api_key: str
    tts_model: str
    tts_model_alt: str
    tts_voices: list[str]
    budget_usd: float
    cfg: dict = field(default_factory=dict)  # contenu de configs/base.yaml


def load_settings(config_path: str = "configs/base.yaml", dotenv_path: str = ".env") -> Settings:
    load_dotenv(dotenv_path)
    e = os.environ
    return Settings(
        stt_base_url=e.get("STT_BASE_URL", ""), stt_api_key=e.get("STT_API_KEY", ""),
        stt_model=e.get("STT_MODEL", "whisper-large-v3-turbo"),
        openrouter_base_url=e.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        openrouter_api_key=e.get("OPENROUTER_API_KEY", ""),
        llm_model=e.get("LLM_MODEL", ""), llm_model_alt=e.get("LLM_MODEL_ALT", ""),
        tts_base_url=e.get("TTS_BASE_URL", "https://openrouter.ai/api/v1"), tts_api_key=e.get("TTS_API_KEY", ""),
        tts_model=e.get("TTS_MODEL", ""), tts_model_alt=e.get("TTS_MODEL_ALT", ""),
        tts_voices=[v.strip() for v in e.get("TTS_VOICES", "").split(",") if v.strip()],
        budget_usd=float(e.get("BUDGET_MAX_EUR", "10")),
        cfg=yaml.safe_load(Path(config_path).read_text(encoding="utf-8")),
    )


def missing_for_live(s: Settings) -> list[str]:
    """Variables à remplir dans .env pour un appel réel (jamais leurs valeurs)."""
    need = {"STT_BASE_URL": s.stt_base_url, "STT_API_KEY": s.stt_api_key, "OPENROUTER_API_KEY": s.openrouter_api_key,
            "LLM_MODEL": s.llm_model, "TTS_API_KEY": s.tts_api_key, "TTS_MODEL": s.tts_model,
            "TTS_VOICES": ",".join(s.tts_voices)}
    return [k for k, v in need.items() if not v]


def build_system_prompt(s: Settings, today: date | None = None) -> str:
    """Prompt système : consigne de base, date du jour et, si la recherche est active, consigne d'usage de l'outil."""
    c = s.cfg["llm"]
    parts = [c["system_prompt"], c["date_line"].format(date=(today or date.today()).isoformat())]
    if s.cfg.get("search", {}).get("enabled"):
        parts.append(c["search_hint"])
    return " ".join(" ".join(p.split()) for p in parts)

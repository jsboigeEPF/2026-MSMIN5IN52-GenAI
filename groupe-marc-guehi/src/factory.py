"""Construction des briques (réelles ou factices) à partir de la configuration."""
from __future__ import annotations

import httpx

from src.config import Settings, missing_for_live
from src.fakes import FakeLLM, FakeSTT, FakeTTS
from src.fallback.stt import FallbackSTT
from src.llm.openrouter import OpenRouterLLM
from src.metrics.cost import load_prices
from src.search.openrouter_web import OpenRouterWebSearch
from src.stt.school_whisper import SchoolWhisper
from src.tts.minimax import MiniMaxTTS


def make_client() -> httpx.AsyncClient:
    """Un seul client partagé : connexions maintenues (TLS déjà négocié) pour les appels suivants."""
    return httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60))


def build_providers(s: Settings, voice: str, client: httpx.AsyncClient | None, on_event, dry_run: bool):
    """STT (en repli sur OpenRouter), LLM (avec recherche web si `search.enabled`) et TTS."""
    if dry_run:
        return FakeSTT(on_event=on_event), FakeLLM(on_event=on_event), FakeTTS(voice, on_event=on_event)
    if missing := missing_for_live(s):
        raise SystemExit(f"Variables manquantes dans .env : {', '.join(missing)}")
    prices = load_prices()
    llm_cfg, search_cfg, stt_cfg = s.cfg.get("llm", {}), s.cfg.get("search", {}), s.cfg.get("stt", {})
    lang = stt_cfg.get("language", "fr")
    stt = SchoolWhisper(client, s.stt_base_url, s.stt_api_key, s.stt_model, language=lang, on_event=on_event)
    if stt_cfg.get("fallback_model"):
        backup = SchoolWhisper(client, s.openrouter_base_url, s.openrouter_api_key, stt_cfg["fallback_model"],
                               language=lang, retries=0, on_event=on_event, name="openrouter-asr")
        stt = FallbackSTT(stt, backup, on_event=on_event)
    searcher = None
    if search_cfg.get("enabled"):
        searcher = OpenRouterWebSearch(client, s.openrouter_base_url, s.openrouter_api_key, s.llm_model,
                                       language=search_cfg.get("language", "français"), max_results=search_cfg.get("max_results", 3),
                                       max_tokens=search_cfg.get("max_tokens", 250), timeout_s=search_cfg.get("timeout_s", 20),
                                       prices=prices, on_event=on_event)
    llm = OpenRouterLLM(client, s.openrouter_base_url, s.openrouter_api_key, s.llm_model, s.llm_model_alt or None,
                        max_tokens=llm_cfg.get("max_tokens", 200), temperature=llm_cfg.get("temperature", 0.6),
                        prices=prices, on_event=on_event, searcher=searcher)
    tts = MiniMaxTTS(client, s.tts_base_url, s.tts_api_key, s.tts_model, voice, speed=s.cfg.get("tts", {}).get("speeds", {}).get(voice),
                     prices=prices, on_event=on_event)  # débit par voix (la voix féminine française est plus lente)
    return stt, llm, tts

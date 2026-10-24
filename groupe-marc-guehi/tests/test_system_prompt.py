from datetime import date

from src.config import build_system_prompt, load_settings


def settings(search_enabled):
    s = load_settings(dotenv_path="/nonexistent")
    s.cfg["search"] = {"enabled": search_enabled}
    return s


def test_prompt_has_date_and_search_hint_when_search_is_enabled():
    prompt = build_system_prompt(settings(True), date(2026, 10, 22))
    assert "français" in prompt and "2026-10-22" in prompt and "web_search" in prompt and "\n" not in prompt


def test_no_search_hint_when_search_is_disabled():
    assert "web_search" not in build_system_prompt(settings(False))


def test_search_hint_asks_for_spoken_announce():
    assert "announce" in build_system_prompt(settings(True))

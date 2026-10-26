from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import Settings

ENV_KEYS = [
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "PLANNER_MODEL",
    "CODER_MODEL",
    "SUMMARIZER_MODEL",
    "USD_EUR_RATE",
    "MAX_COST_EUR_PER_RUN",
    "APPROVAL_TIMEOUT_SEC",
    "MAX_FIX",
    "MAX_REVISIONS",
    "SANDBOX_MODE",
    "SANDBOX_TIMEOUT_SEC",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults_without_env() -> None:
    s = Settings(_env_file=None)
    assert s.OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1"
    assert s.OPENROUTER_API_KEY is None
    assert s.PLANNER_MODEL == s.CODER_MODEL == s.SUMMARIZER_MODEL == ""
    assert s.MAX_FIX == 10 and s.MAX_REVISIONS == 5
    assert s.SANDBOX_MODE == "subprocess"  # code runs only after a human approved it
    assert s.BUDGET_EUR_TOTAL == 10.0


def test_empty_values_from_env_example_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FIX", "")
    monkeypatch.setenv("USD_EUR_RATE", "")
    s = Settings(_env_file=None)
    assert s.MAX_FIX == 10
    assert s.USD_EUR_RATE > 0


def test_reads_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PLANNER_MODEL=vendor/planner-model\nMAX_REVISIONS=7\nSANDBOX_MODE=docker\n")
    s = Settings(_env_file=env)
    assert s.PLANNER_MODEL == "vendor/planner-model"
    assert s.MAX_REVISIONS == 7
    assert s.SANDBOX_MODE == "docker"


def test_api_key_never_appears_in_repr_or_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-secret")
    s = Settings(_env_file=None)
    assert s.OPENROUTER_API_KEY is not None
    assert s.OPENROUTER_API_KEY.get_secret_value() == "sk-or-test-secret"
    assert "sk-or-test-secret" not in repr(s)
    assert "sk-or-test-secret" not in s.model_dump_json()


@pytest.mark.parametrize(
    ("key", "value"),
    [("SANDBOX_MODE", "vm"), ("MAX_FIX", "-1"), ("USD_EUR_RATE", "0"), ("APPROVAL_TIMEOUT_SEC", "0")],
)
def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_usd_to_eur(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USD_EUR_RATE", "0.5")
    assert Settings(_env_file=None).usd_to_eur(3.0) == pytest.approx(1.5)


def test_legacy_auto_sandbox_mode_means_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "auto")
    assert Settings(_env_file=None).SANDBOX_MODE == "subprocess"

import logging

import pytest

from src.metrics.cost import BudgetExceeded, CostLedger, estimate_tts


def test_ledger_accumulates_and_persists(tmp_path):
    p = str(tmp_path / "l.jsonl")
    led = CostLedger(p, budget_usd=1.0)
    led.add("tts", "m", 0.25, chars=10)
    led.add("llm", "m", 0.25)
    assert CostLedger(p, budget_usd=1.0).total_usd == pytest.approx(0.5)


def test_over_budget_only_warns_by_default(tmp_path, caplog):
    led = CostLedger(str(tmp_path / "l.jsonl"), budget_usd=0.1)
    with caplog.at_level(logging.WARNING):
        led.add("tts", "m", 0.5)
    assert "plafond" in caplog.text


def test_hard_stop_raises(tmp_path):
    led = CostLedger(str(tmp_path / "l.jsonl"), budget_usd=0.1, hard_stop=True)
    with pytest.raises(BudgetExceeded):
        led.add("tts", "m", 0.5)


def test_estimate_tts_per_char():
    assert estimate_tts({"tts": {"m": {"per_char": 0.001}}}, "m", 100) == pytest.approx(0.1)
    assert estimate_tts({}, "inconnu", 100) == 0.0

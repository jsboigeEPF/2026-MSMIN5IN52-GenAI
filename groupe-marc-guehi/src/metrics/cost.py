"""Estimation et journal des coûts cloud. Le plafond est indicatif : dépassement signalé, jamais bloquant par défaut."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

log = logging.getLogger("voice_agent.cost")


class BudgetExceeded(RuntimeError):
    pass


def load_prices(path: str = "configs/pricing.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def estimate_tts(prices: dict, model: str, chars: int) -> float:
    return chars * prices.get("tts", {}).get(model, {}).get("per_char", 0.0)


def estimate_llm(prices: dict, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = prices.get("llm", {}).get(model, {})
    return prompt_tokens * p.get("prompt", 0.0) + completion_tokens * p.get("completion", 0.0)


class CostLedger:
    def __init__(self, path: str = "data/cost_ledger.jsonl", budget_usd: float = 10.0, hard_stop: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.budget_usd, self.hard_stop = budget_usd, hard_stop
        self._total = 0.0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._total += json.loads(line).get("cost_usd", 0.0)
        self._warned = False

    @property
    def total_usd(self) -> float:
        return self._total

    def add(self, stage: str, provider: str, cost_usd: float, **detail) -> None:
        self._total += cost_usd
        rec = {"ts_utc": datetime.now(UTC).isoformat(timespec="seconds"), "stage": stage,
               "provider": provider, "cost_usd": cost_usd, "total_usd": self._total, **detail}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self._total > self.budget_usd:
            if self.hard_stop:
                raise BudgetExceeded(f"plafond {self.budget_usd:.2f} $ dépassé ({self._total:.2f} $)")
            if not self._warned:
                log.warning("plafond indicatif de %.2f $ dépassé (%.2f $)", self.budget_usd, self._total)
                self._warned = True

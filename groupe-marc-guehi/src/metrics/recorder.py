"""Point d'entrée unique des événements des briques : traces JSONL + compteur de coût."""
from __future__ import annotations

from .cost import CostLedger
from .tracer import Tracer


class Recorder:
    """Les briques appellent recorder(event, **data). Le stage est le préfixe de l'événement (tts_request -> tts).
    L'événement spécial "usage" alimente le coût : stage=, provider=, cost_usd=."""

    def __init__(self, tracer: Tracer | None = None, ledger: CostLedger | None = None) -> None:
        self.tracer, self.ledger = tracer, ledger
        self.turn = 0
        self.events: list[tuple[str, float, dict]] = []  # (event, t_ms, data) pour l'affichage en console

    def __call__(self, event: str, **data) -> None:
        if event == "usage":
            if self.ledger:
                self.ledger.add(data["stage"], data["provider"], data["cost_usd"],
                                **{k: v for k, v in data.items() if k not in ("stage", "provider", "cost_usd")})
            return
        stage = event.split("_", 1)[0]
        t = self.tracer.now_ms() if self.tracer else 0.0
        self.events.append((event, t, data))
        if self.tracer:
            self.tracer.emit(self.turn, event, stage, provider=str(data.pop("provider", "")), data=data)

    def last(self, event: str) -> float | None:
        return next((t for e, t, _ in reversed(self.events) if e == event), None)

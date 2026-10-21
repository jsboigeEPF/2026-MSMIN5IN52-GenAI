"""LLM : OpenRouter /chat/completions en streaming, raisonnement coupé, repli sur un 2e modèle avant le 1er token."""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable

import httpx

from src.metrics.cost import estimate_llm


class LLMError(RuntimeError):
    pass


class OpenRouterLLM:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str, fallback_model: str | None = None,
                 max_tokens: int = 200, temperature: float = 0.6, prices: dict | None = None,
                 on_event: Callable | None = None) -> None:
        self.client, self.url = client, base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.models = [m for m in (model, fallback_model) if m]
        self.max_tokens, self.temperature, self.prices = max_tokens, temperature, prices or {}
        self.emit = on_event or (lambda *a, **k: None)

    async def warmup(self) -> None:
        async for _ in self.stream([{"role": "user", "content": "ok"}], max_tokens=1):
            pass

    async def stream(self, messages: list[dict], max_tokens: int | None = None) -> AsyncIterator[str]:
        last: LLMError | None = None
        for model in self.models:
            started = False
            try:
                async for delta in self._stream_one(model, messages, max_tokens or self.max_tokens):
                    started = True
                    yield delta
                return
            except LLMError as e:
                if started:  # flux déjà entamé : on ne peut pas reprendre proprement
                    raise
                last = e
                self.emit("llm_fallback", provider=model, error=str(e)[:200])
        raise last or LLMError("aucun modèle LLM configuré")

    async def _stream_one(self, model: str, messages: list[dict], max_tokens: int) -> AsyncIterator[str]:
        body = {"model": model, "stream": True, "max_tokens": max_tokens, "temperature": self.temperature,
                "messages": messages, "reasoning": {"enabled": False}, "provider": {"sort": "latency"},
                "stream_options": {"include_usage": True}}
        self.emit("llm_request", provider=model, n_messages=len(messages))
        t0 = time.perf_counter()
        first, usage = False, {}
        async with self.client.stream("POST", self.url, headers=self.headers, json=body, timeout=60) as r:
            if r.status_code != 200:
                raise LLMError(f"LLM HTTP {r.status_code} {(await r.aread())[:200]!r}")
            async for line in r.aiter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                ev = json.loads(line[6:])
                if "error" in ev:  # erreur dans le flux malgré HTTP 200
                    raise LLMError(f"LLM erreur de flux {ev['error'].get('code')} {ev['error'].get('message')}")
                usage = ev.get("usage") or usage
                for ch in ev.get("choices", []):
                    d = ch.get("delta", {}).get("content")
                    if d:
                        if not first:
                            first = True
                            self.emit("llm_first_token", provider=model, ms=round((time.perf_counter() - t0) * 1000, 1))
                        yield d
        self.emit("llm_done", provider=model, ms=round((time.perf_counter() - t0) * 1000, 1),
                  prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
        cost = usage.get("cost")
        if cost is None:
            cost = estimate_llm(self.prices, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        self.emit("usage", stage="llm", provider=model, cost_usd=cost, prompt_tokens=usage.get("prompt_tokens"),
                  completion_tokens=usage.get("completion_tokens"))

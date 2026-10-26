"""Recherche web : OpenRouter /chat/completions avec le plugin `web` (résultats injectés dans le contexte du modèle)."""
from __future__ import annotations

from collections.abc import Callable

import httpx

from src.metrics.clock import elapsed_ms, now_ns
from src.metrics.cost import estimate_llm


class SearchError(RuntimeError):
    pass


SYSTEM = ("You are a web research tool for a voice assistant. Use the web results to answer the query with current, "
          "concrete facts (figures, dates, names) in {language}. 2 to 4 short sentences, plain text: no markdown, "
          "no URLs, no source names, no introduction. If the results do not contain the answer, say so in one sentence.")


class OpenRouterWebSearch:
    name = "openrouter-web"

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str, language: str = "français",
                 max_results: int = 3, max_tokens: int = 250, timeout_s: float = 20.0, prices: dict | None = None,
                 on_event: Callable | None = None) -> None:
        self.client, self.url = client, base_url.rstrip("/") + "/chat/completions"
        self.headers, self.model, self.language = {"Authorization": f"Bearer {api_key}"}, model, language
        self.max_results, self.max_tokens, self.timeout_s, self.prices = max_results, max_tokens, timeout_s, prices or {}
        self.emit = on_event or (lambda *a, **k: None)

    async def search(self, query: str) -> str:
        body = {"model": self.model, "max_tokens": self.max_tokens, "temperature": 0.2, "reasoning": {"enabled": False},
                "plugins": [{"id": "web", "max_results": self.max_results}],
                "messages": [{"role": "system", "content": SYSTEM.format(language=self.language)},
                             {"role": "user", "content": query}]}
        self.emit("search_request", provider=self.model, chars=len(query))
        t0 = now_ns()
        try:
            r = await self.client.post(self.url, headers=self.headers, json=body, timeout=self.timeout_s)
        except httpx.HTTPError as e:
            raise SearchError(f"recherche : {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise SearchError(f"recherche HTTP {r.status_code} {r.text[:200]!r}")
        data = r.json()
        if "error" in data:
            raise SearchError(f"recherche erreur {data['error'].get('code')} {data['error'].get('message')}")
        text = (data["choices"][0]["message"].get("content") or "").strip()
        usage = data.get("usage") or {}
        self.emit("search_done", provider=self.model, ms=round(elapsed_ms(t0), 1), chars=len(text))
        cost = usage.get("cost")
        if cost is None:
            cost = estimate_llm(self.prices, self.model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        self.emit("usage", stage="search", provider=self.model, cost_usd=cost)
        if not text:
            raise SearchError("recherche : réponse vide")
        return text

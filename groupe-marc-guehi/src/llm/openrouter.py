"""LLM : OpenRouter /chat/completions en streaming, raisonnement coupé, repli sur un 2e modèle avant le 1er token.
Avec un `searcher`, le modèle dispose de l'outil `web_search` (une recherche au plus par réponse)."""
from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Callable

import httpx

from src.interfaces import SearchNotice, WebSearch
from src.metrics.cost import estimate_llm
from src.search.openrouter_web import SearchError


class LLMError(RuntimeError):
    pass


WEB_SEARCH_TOOL = {"type": "function", "function": {
    "name": "web_search",
    "description": "Recherche sur internet. Pour toute information actuelle ou que tu ne connais pas de mémoire "
                   "(météo, actualité, scores, prix, horaires, événements récents). Search the web for current information.",
    "parameters": {"type": "object", "properties": {"query": {
        "type": "string", "description": "Requête autonome et précise, dans la langue de la conversation (lieu, date, sujet)."}},
        "required": ["query"]}}}
SEARCH_UNAVAILABLE = "SEARCH UNAVAILABLE: the search failed. Tell the user briefly that you could not check, and do not guess."


DSML = re.compile(r"<[｜|]\s*DSML")
DSML_QUERY = re.compile(r'name="query"[^>]*>\s*([^<]+)')


class ToolMarkupGuard:
    """DeepSeek écrit parfois l'appel d'outil en texte (balises DSML) au lieu du champ `tool_calls`, surtout quand la
    question porte sur plusieurs sujets : ce texte ne doit jamais partir vers le TTS. On le retient et on en extrait les requêtes."""

    def __init__(self) -> None:
        self._pending, self.captured, self.active = "", "", False

    def feed(self, text: str) -> str:
        """Retourne la partie de `text` qui peut être lue à voix haute."""
        if self.active:
            self.captured += text
            return ""
        s = self._pending + text
        if m := DSML.search(s):
            self.active, self.captured, self._pending = True, s[m.start():], ""
            return s[:m.start()]
        k = s.rfind("<")
        if k != -1 and len(s) - k < 12:  # peut-être le début d'une balise coupée entre deux deltas
            self._pending = s[k:]
            return s[:k]
        self._pending = ""
        return s

    def flush(self) -> str:
        out, self._pending = ("", "") if self.active else (self._pending, "")
        return out

    def queries(self) -> list[str]:
        return [q.strip() for q in DSML_QUERY.findall(self.captured) if q.strip()]


def _query_of(args: str) -> str:
    try:
        return str(json.loads(args or "{}").get("query", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


class OpenRouterLLM:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str, fallback_model: str | None = None,
                 max_tokens: int = 200, temperature: float = 0.6, prices: dict | None = None,
                 on_event: Callable | None = None, searcher: WebSearch | None = None) -> None:
        self.client, self.url = client, base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.models = [m for m in (model, fallback_model) if m]
        self.max_tokens, self.temperature, self.prices = max_tokens, temperature, prices or {}
        self.emit = on_event or (lambda *a, **k: None)
        self.searcher = searcher

    async def warmup(self) -> None:
        async for _ in self.stream([{"role": "user", "content": "ok"}], max_tokens=1, allow_search=False):
            pass

    async def stream(self, messages: list[dict], max_tokens: int | None = None,
                     allow_search: bool = True) -> AsyncIterator[str | SearchNotice]:
        last: LLMError | None = None
        for model in self.models:
            started = False
            try:
                async for delta in self._stream_one(model, messages, max_tokens or self.max_tokens, allow_search):
                    started = True
                    yield delta
                return
            except LLMError as e:
                if started:  # flux déjà entamé : on ne peut pas reprendre proprement
                    raise
                last = e
                self.emit("llm_fallback", provider=model, error=str(e)[:200])
        raise last or LLMError("aucun modèle LLM configuré")

    async def _stream_one(self, model: str, messages: list[dict], max_tokens: int,
                          allow_search: bool = True) -> AsyncIterator[str | SearchNotice]:
        calls: dict[int, dict] = {}
        use_tools = allow_search and self.searcher is not None
        async for delta in self._request(model, messages, max_tokens, use_tools, calls):
            yield delta
        if not calls:
            return
        call = calls[min(calls)]  # une seule recherche par réponse : les requêtes parallèles sont regroupées en une
        queries = [q for c in (calls[i] for i in sorted(calls)) if (q := _query_of(c["args"]))]
        query = " ; ".join(queries) or next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        self.emit("llm_tool_call", provider=model, tool=call["name"], chars=len(query))
        yield SearchNotice(query)
        try:
            result = await self.searcher.search(query)
        except SearchError as e:
            self.emit("search_error", provider=model, error=str(e)[:200])
            result = SEARCH_UNAVAILABLE
        follow = messages + [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["args"] or "{}"}}]},
            {"role": "tool", "tool_call_id": call["id"], "content": result}]
        async for delta in self._request(model, follow, max_tokens, False, {}):
            yield delta

    async def _request(self, model: str, messages: list[dict], max_tokens: int, use_tools: bool,
                       calls: dict[int, dict]) -> AsyncIterator[str]:
        """Une requête en flux. Les appels d'outil demandés par le modèle sont accumulés dans `calls` (index -> id, name, args)."""
        body = {"model": model, "stream": True, "max_tokens": max_tokens, "temperature": self.temperature,
                "messages": messages, "reasoning": {"enabled": False}, "provider": {"sort": "latency"},
                "stream_options": {"include_usage": True}}
        if use_tools:
            body["tools"] = [WEB_SEARCH_TOOL]
        guard = ToolMarkupGuard()
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
                    for tc in ch.get("delta", {}).get("tool_calls") or []:
                        c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                        c["id"] = tc.get("id") or c["id"]
                        fn = tc.get("function") or {}
                        c["name"] = fn.get("name") or c["name"]
                        c["args"] += fn.get("arguments") or ""
                    d = guard.feed(ch.get("delta", {}).get("content") or "")
                    if d:
                        if not first:
                            first = True
                            self.emit("llm_first_token", provider=model, ms=round((time.perf_counter() - t0) * 1000, 1))
                        yield d
        if tail := guard.flush():
            yield tail
        if guard.active:
            self.emit("llm_tool_markup", provider=model, chars=len(guard.captured))
            if use_tools and not calls:  # l'appel d'outil était écrit en texte : on le traite comme un vrai appel
                calls[0] = {"id": "call-markup", "name": "web_search", "args": json.dumps({"query": " ; ".join(guard.queries())})}
        self.emit("llm_done", provider=model, ms=round((time.perf_counter() - t0) * 1000, 1),
                  prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
        cost = usage.get("cost")
        if cost is None:
            cost = estimate_llm(self.prices, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        self.emit("usage", stage="llm", provider=model, cost_usd=cost, prompt_tokens=usage.get("prompt_tokens"),
                  completion_tokens=usage.get("completion_tokens"))

"""LLM : OpenRouter /chat/completions en streaming, raisonnement coupé, repli sur un 2e modèle avant le 1er token.
Avec un `searcher`, le modèle dispose de l'outil `web_search` (une recherche au plus par réponse)."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable

import httpx

from src.interfaces import LengthCut, SearchNotice, WebSearch
from src.metrics.clock import elapsed_ms, now_ns
from src.metrics.cost import estimate_llm
from src.search.openrouter_web import SearchError


class LLMError(RuntimeError):
    pass


WEB_SEARCH_TOOL = {"type": "function", "function": {
    "name": "web_search",
    "description": "Recherche sur internet. Pour toute information actuelle ou que tu ne connais pas de mémoire "
                   "(météo, actualité, scores, prix, horaires, événements récents). Search the web for current information.",
    "parameters": {"type": "object", "properties": {"query": {
        "type": "string", "description": "Requête autonome et précise, dans la langue de la conversation (lieu, date, sujet)."},
        "announce": {"type": "string", "description": "Phrase très courte (4 à 9 mots), dite à voix haute à l'utilisateur pendant "
                     "la recherche, qui dit ce que tu cherches. Ex. : « Je regarde la météo à Lyon demain. »"}},
        "required": ["query", "announce"]}}}
NO_SEARCH_TOOL = {"type": "function", "function": {
    "name": "no_search",
    "description": "À appeler à la place de web_search UNIQUEMENT pour la conversation courante (salutation, remerciement, "
                   "reformulation, état d'âme), un calcul, ou une connaissance stable (définition, grammaire, géographie, "
                   "histoire ancienne, science de base). Jamais pour une personne, un club, une entreprise, un lieu précis ou l'actualité.",
    "parameters": {"type": "object", "properties": {}}}}
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


def _arg_of(args: str, key: str) -> str:
    try:
        return str(json.loads(args or "{}").get(key, "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


def _query_of(args: str) -> str:
    return _arg_of(args, "query")


def make_force_search(skip_regex: str, skip_max_words: int) -> Callable[[str], bool]:
    """Politique « chercher d'abord » : le modèle doit appeler `web_search` ou `no_search` ; faux (tool_choice libre) pour les
    énoncés très courts ou composés uniquement de salutations/remerciements (aucun aller-retour de décision)."""
    skip = re.compile(skip_regex, re.IGNORECASE) if skip_regex else None

    def force(text: str) -> bool:
        t = re.sub(r"[^\w' -]", " ", text.lower()).strip()
        return not (len(t.split()) <= skip_max_words or (skip and skip.fullmatch(" ".join(t.split()))))
    return force


class OpenRouterLLM:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str, fallback_model: str | None = None,
                 max_tokens: int = 200, temperature: float = 0.6, prices: dict | None = None,
                 on_event: Callable | None = None, searcher: WebSearch | None = None,
                 force_search: Callable[[str], bool] | None = None, timeout_s: float = 60.0,
                 first_token_timeout_s: float = 0.0) -> None:
        self.timeout_s = timeout_s                        # délai réseau httpx (connexion, lecture entre deux morceaux)
        self.first_token_timeout_s = first_token_timeout_s  # 0 = pas d'échéance ; sinon repli sur le modèle de secours
        self.client, self.url = client, base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.models = [m for m in (model, fallback_model) if m]
        self.max_tokens, self.temperature, self.prices = max_tokens, temperature, prices or {}
        self.emit = on_event or (lambda *a, **k: None)
        self.searcher = searcher
        self.force_search = force_search  # si vrai pour le dernier message, 1re requête avec tool_choice=required

    async def warmup(self) -> None:
        async for _ in self.stream([{"role": "user", "content": "ok"}], max_tokens=1, allow_search=False):
            pass

    async def stream(self, messages: list[dict], max_tokens: int | None = None,
                     allow_search: bool = True) -> AsyncIterator[str | SearchNotice]:
        last: Exception | None = None
        for model in self.models:
            started = False
            try:
                async for delta in self._stream_one(model, messages, max_tokens or self.max_tokens, allow_search):
                    started = True
                    yield delta
                return
            except (LLMError, httpx.HTTPError) as e:  # erreur du service ou du réseau (connexion, délai, flux coupé)
                if started:  # flux déjà entamé : on ne peut pas reprendre proprement
                    raise
                last = e
                self.emit("llm_fallback", provider=model, error=f"{type(e).__name__}: {e}"[:200])
        if last is None:
            raise LLMError("aucun modèle LLM configuré")
        raise LLMError(f"aucun modèle LLM n'a répondu ({type(last).__name__}: {last})"[:300]) from last

    async def _stream_one(self, model: str, messages: list[dict], max_tokens: int,
                          allow_search: bool = True) -> AsyncIterator[str | SearchNotice]:
        calls: dict[int, dict] = {}
        use_tools = allow_search and self.searcher is not None
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        force = use_tools and self.force_search is not None and self.force_search(last_user)
        async for delta in self._request(model, messages, max_tokens, use_tools, calls, force):
            yield delta
        searches = {i: c for i, c in calls.items() if c["name"] != "no_search"}
        if calls and not searches:  # recherche forcée, mais le modèle juge qu'elle est inutile : réponse directe sans outil
            self.emit("llm_no_search", provider=model)
            async for delta in self._request(model, messages, max_tokens, False, {}):
                yield delta
            return
        calls = searches or calls
        if not calls:
            return
        call = calls[min(calls)]  # une seule recherche par réponse : les requêtes parallèles sont regroupées en une
        queries = [q for c in (calls[i] for i in sorted(calls)) if (q := _query_of(c["args"]))]
        query = " ; ".join(queries) or next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        self.emit("llm_tool_call", provider=model, tool=call["name"], chars=len(query))
        announce = _arg_of(call["args"], "announce")  # une seule annonce : celle du premier appel
        yield SearchNotice(query, announce)
        try:
            result = await self.searcher.search(query)
        except SearchError as e:
            self.emit("search_error", provider=model, error=str(e)[:200])
            result = SEARCH_UNAVAILABLE
        follow = messages + [
            {"role": "assistant", "content": announce or None, "tool_calls": [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["args"] or "{}"}}]},
            {"role": "tool", "tool_call_id": call["id"], "content": result}]
        async for delta in self._request(model, follow, max_tokens, False, {}, first_token_timeout_s=0):  # SearchNotice déjà parti : pas de repli possible, donc pas d'échéance
            yield delta

    async def _request(self, model: str, messages: list[dict], max_tokens: int, use_tools: bool,
                       calls: dict[int, dict], force: bool = False, first_token_timeout_s: float | None = None) -> AsyncIterator[str]:
        """Une requête en flux. Les appels d'outil demandés par le modèle sont accumulés dans `calls` (index -> id, name, args)."""
        body = {"model": model, "stream": True, "max_tokens": max_tokens, "temperature": self.temperature,
                "messages": messages, "reasoning": {"enabled": False}, "provider": {"sort": "latency"},
                "stream_options": {"include_usage": True}}
        if use_tools:
            body["tools"] = [WEB_SEARCH_TOOL, NO_SEARCH_TOOL] if force else [WEB_SEARCH_TOOL]
            if force:
                body["tool_choice"] = "required"
        guard = ToolMarkupGuard()
        self.emit("llm_request", provider=model, n_messages=len(messages))
        t0 = now_ns()
        first, usage, finish = False, {}, None
        try:
            limit = self.first_token_timeout_s if first_token_timeout_s is None else first_token_timeout_s
            async with asyncio.timeout(limit or None) as deadline:  # échéance du 1er signe de vie du modèle
                async with self.client.stream("POST", self.url, headers=self.headers, json=body, timeout=self.timeout_s) as r:
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
                            finish = ch.get("finish_reason") or finish
                            for tc in ch.get("delta", {}).get("tool_calls") or []:
                                deadline.reschedule(None)
                                c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                                c["id"] = tc.get("id") or c["id"]
                                fn = tc.get("function") or {}
                                c["name"] = fn.get("name") or c["name"]
                                c["args"] += fn.get("arguments") or ""
                            d = guard.feed(ch.get("delta", {}).get("content") or "")
                            if d:
                                deadline.reschedule(None)
                                if not first:
                                    first = True
                                    self.emit("llm_first_token", provider=model, ms=round(elapsed_ms(t0), 1))
                                yield d
        except TimeoutError as e:
            raise LLMError(f"pas de premier jeton après {limit} s") from e
        if tail := guard.flush():
            yield tail
        if finish == "length" and not calls:
            self.emit("llm_length", provider=model, max_tokens=max_tokens)
            yield LengthCut()
        if guard.active:
            self.emit("llm_tool_markup", provider=model, chars=len(guard.captured))
            if use_tools and not calls:  # l'appel d'outil était écrit en texte : on le traite comme un vrai appel
                calls[0] = {"id": "call-markup", "name": "web_search", "args": json.dumps({"query": " ; ".join(guard.queries())})}
        self.emit("llm_done", provider=model, ms=round(elapsed_ms(t0), 1),
                  prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
        cost = usage.get("cost")
        if cost is None:
            cost = estimate_llm(self.prices, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        self.emit("usage", stage="llm", provider=model, cost_usd=cost, prompt_tokens=usage.get("prompt_tokens"),
                  completion_tokens=usage.get("completion_tokens"))

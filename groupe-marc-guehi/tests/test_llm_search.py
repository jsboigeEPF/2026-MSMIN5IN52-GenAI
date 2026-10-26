import json

import httpx
import pytest

from src.interfaces import SearchNotice
from src.llm.openrouter import SEARCH_UNAVAILABLE, OpenRouterLLM, ToolMarkupGuard
from src.search.openrouter_web import OpenRouterWebSearch, SearchError


def sse(*events: dict) -> bytes:
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode()


def delta(**d) -> dict:
    return {"choices": [{"delta": d}]}


TOOL_CALL = sse(delta(tool_calls=[{"index": 0, "id": "call-1", "function": {"name": "web_search", "arguments": ""}}]),
                delta(tool_calls=[{"index": 0, "function": {"arguments": '{"query": "météo Lausa'}}]),
                delta(tool_calls=[{"index": 0, "function": {"arguments": 'nne"}'}}]))
ANSWER = sse(delta(content="Il fait "), delta(content="10 degrés."), {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3, "cost": 0.0}})


class FakeSearch:
    def __init__(self, result="10 °C, nuageux.", error=None):
        self.result, self.error, self.queries = result, error, []

    async def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


def make(handler, searcher):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterLLM(client, "http://x", "k", "m", searcher=searcher)


async def collect(llm, **kw):
    return [d async for d in llm.stream([{"role": "user", "content": "météo ?"}], **kw)]


async def test_tool_call_runs_search_then_answers():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=TOOL_CALL if len(bodies) == 1 else ANSWER)

    search = FakeSearch()
    out = await collect(make(handler, search))
    assert out == [SearchNotice("météo Lausanne"), "Il fait ", "10 degrés."]
    assert search.queries == ["météo Lausanne"]
    assert "tools" in bodies[0] and "tools" not in bodies[1]  # une seule recherche par réponse
    assistant, tool = bodies[1]["messages"][-2:]
    assert assistant["tool_calls"][0]["id"] == "call-1" and tool == {"role": "tool", "tool_call_id": "call-1", "content": "10 °C, nuageux."}


async def test_plain_answer_makes_a_single_request():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, content=ANSWER)

    assert await collect(make(handler, FakeSearch())) == ["Il fait ", "10 degrés."]
    assert len(calls) == 1


async def test_search_failure_tells_the_model_not_to_guess():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=TOOL_CALL if len(bodies) == 1 else ANSWER)

    await collect(make(handler, FakeSearch(error=SearchError("down"))))
    assert bodies[1]["messages"][-1]["content"] == SEARCH_UNAVAILABLE


async def test_search_disabled_or_warmup_sends_no_tools():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=ANSWER)

    await collect(make(handler, None))
    await make(handler, FakeSearch()).warmup()
    assert all("tools" not in b for b in bodies)


def search_client(response):
    return OpenRouterWebSearch(httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response(req))),
                               "http://x", "k", "m", language="français")


async def test_web_search_returns_text_and_uses_web_plugin():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": " 10 °C. "}}], "usage": {"cost": 0.007}})

    assert await search_client(handler).search("météo") == "10 °C."
    assert seen["plugins"][0]["id"] == "web" and "français" in seen["messages"][0]["content"]


@pytest.mark.parametrize("response", [httpx.Response(500, text="boom"),
                                      httpx.Response(200, json={"error": {"code": 429, "message": "limit"}}),
                                      httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})])
async def test_web_search_errors(response):
    with pytest.raises(SearchError):
        await search_client(lambda req: response).search("x")


DSML_TEXT = ('<｜DSML｜function_calls><｜DSML｜invoke name="web_search"><｜DSML｜parameter name="query" string="true">'
             'dernière course F1</｜DSML｜parameter></｜DSML｜invoke><｜DSML｜invoke name="web_search">'
             '<｜DSML｜parameter name="query" string="true">météo Paris</｜DSML｜parameter></｜DSML｜invoke></｜DSML｜function_calls>')


def chunks(text, n=7):
    return [text[i:i + n] for i in range(0, len(text), n)]


def test_guard_hides_markup_split_across_deltas_and_extracts_queries():
    g = ToolMarkupGuard()
    spoken = "".join(g.feed(c) for c in chunks("Voyons. " + DSML_TEXT, 3)) + g.flush()
    assert spoken == "Voyons. " and g.active and g.queries() == ["dernière course F1", "météo Paris"]


def test_guard_lets_normal_text_through():
    g = ToolMarkupGuard()
    spoken = "".join(g.feed(c) for c in chunks("Si 3 < 5 alors <b>oui</b>.", 2)) + g.flush()
    assert spoken == "Si 3 < 5 alors <b>oui</b>." and not g.active


async def test_markup_tool_call_is_executed_as_a_real_one_and_never_spoken():
    bodies = []
    markup = sse(*[delta(content=c) for c in chunks(DSML_TEXT)])

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=markup if len(bodies) == 1 else ANSWER)

    search = FakeSearch()
    out = await collect(make(handler, search))
    assert out == [SearchNotice("dernière course F1 ; météo Paris"), "Il fait ", "10 degrés."]
    assert search.queries == ["dernière course F1 ; météo Paris"]


async def test_announce_is_forwarded_and_kept_in_the_tool_call_message():
    bodies = []
    call = sse(delta(tool_calls=[{"index": 0, "id": "call-1", "function": {
        "name": "web_search", "arguments": json.dumps({"query": "météo Lyon demain", "announce": "Je regarde la météo à Lyon."})}}]))

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=call if len(bodies) == 1 else ANSWER)

    out = await collect(make(handler, FakeSearch()))
    assert out[0] == SearchNotice("météo Lyon demain", "Je regarde la météo à Lyon.")
    assert bodies[1]["messages"][-2]["content"] == "Je regarde la météo à Lyon."  # le modèle sait qu'il l'a déjà dit
    assert "announce" in bodies[0]["tools"][0]["function"]["parameters"]["properties"]


async def test_force_search_sets_tool_choice_except_small_talk():
    from src.llm.openrouter import make_force_search
    force = make_force_search("((bonjour|merci|beaucoup|comment vas-tu) ?)+", 0)
    assert force("Parle-moi de Ronald Araujo, le défenseur") and not force("Bonjour, comment vas-tu ?") and force("Parle de Pedri")
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=ANSWER)

    llm = OpenRouterLLM(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "http://x", "k", "m",
                        searcher=FakeSearch(), force_search=force)
    _ = [d async for d in llm.stream([{"role": "user", "content": "Parle-moi de Ronald Araujo, le défenseur"}])]
    _ = [d async for d in llm.stream([{"role": "user", "content": "merci beaucoup"}])]
    assert bodies[0]["tool_choice"] == "required" and "tool_choice" not in bodies[1]
    assert [t["function"]["name"] for t in bodies[0]["tools"]] == ["web_search", "no_search"]


async def test_no_search_answers_directly_without_notice():
    NO = sse(delta(tool_calls=[{"index": 0, "id": "c", "function": {"name": "no_search", "arguments": "{}"}}]))
    n = []

    def handler(req):
        n.append(json.loads(req.content))
        return httpx.Response(200, content=NO if len(n) == 1 else ANSWER)

    search = FakeSearch()
    llm = OpenRouterLLM(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "http://x", "k", "m",
                        searcher=search, force_search=lambda t: True)
    out = [d async for d in llm.stream([{"role": "user", "content": "combien font 2 fois 3"}])]
    assert out == ["Il fait ", "10 degrés."] and search.queries == [] and "tools" not in n[1]

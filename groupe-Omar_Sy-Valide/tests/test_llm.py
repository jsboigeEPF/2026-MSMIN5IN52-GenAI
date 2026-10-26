import json
from dataclasses import replace
from typing import Any

import httpx2
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from semantic_kernel import Kernel

from src.config import Settings
from src.kernel_factory import SERVICE_IDS, build_kernel
from src.llm.client import (
    BudgetExceededError,
    ChatRequest,
    LLMClient,
    LLMProviderError,
    LLMUsage,
    OpenRouterBackend,
    StructuredOutputError,
)
from src.llm.fake import FakeLLM, FakeScriptExhaustedError
from src.models.state import CodePatch, ErrorSummary, PlanOutput
from src.storage.sqlite_store import SqliteStore

PLAN = PlanOutput(target_file="calc.py", steps=["read calc.py", "fix add"])
PATCH = CodePatch(new_content="x = 1\n", rationale="r")


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def request(service_id: str = "planner", user: str = "u") -> ChatRequest:
    return ChatRequest(service_id=service_id, system="s", user=user, output_type=PlanOutput)


# --- FakeLLM ------------------------------------------------------------------------------------------


async def test_fake_llm_replays_script_in_order_and_counts_calls() -> None:
    fake = FakeLLM({"planner": [PLAN, {"steps": ["second"]}, '{"steps": []}']})
    contents = [(await fake.complete(request())).content for _ in range(3)]
    assert json.loads(contents[0]) == PLAN.model_dump()
    assert json.loads(contents[1]) == {"steps": ["second"]}
    assert contents[2] == '{"steps": []}'
    assert fake.calls["planner"] == 3
    assert [r.user for r in fake.requests] == ["u", "u", "u"]


async def test_fake_llm_fails_loudly_when_script_is_exhausted() -> None:
    with pytest.raises(FakeScriptExhaustedError, match="coder"):
        await FakeLLM({}).complete(request("coder"))


async def test_fake_summarizer_defaults_to_deterministic_truncation() -> None:
    fake = FakeLLM({}, summary_chars=5)
    response = await fake.complete(ChatRequest("summarizer", "s", "0123456789", ErrorSummary))
    assert ErrorSummary.model_validate_json(response.content).summary == "01234"


# --- LLMClient ----------------------------------------------------------------------------------------


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


def make_client(fake: FakeLLM, store: SqliteStore, exporter: InMemorySpanExporter, **cfg: Any) -> LLMClient:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return LLMClient(Kernel(), fake, store, settings(**cfg), provider.get_tracer("t"))


async def call(client: LLMClient, iteration: int = 0, service_id: str = "planner") -> Any:
    return await client.structured(
        run_id="run-1",
        step="plan",
        iteration=iteration,
        service_id=service_id,
        system="sys",
        user="usr",
        output_type=PlanOutput,
    )


async def test_structured_call_parses_output_and_reports_usage(
    store: SqliteStore, exporter: InMemorySpanExporter
) -> None:
    fake = FakeLLM({"planner": [PLAN]}, usage=LLMUsage(prompt_tokens=10, completion_tokens=5, cost_usd=0.01))
    result = await call(make_client(fake, store, exporter))
    assert result.output == PLAN and not result.cached
    assert (result.usage.prompt_tokens, result.usage.cost_usd) == (10, 0.01)


async def test_same_key_is_replayed_from_cache_without_calling_the_llm(
    store: SqliteStore, exporter: InMemorySpanExporter
) -> None:
    fake = FakeLLM({"planner": [PLAN, PlanOutput(target_file="calc.py", steps=["other"])]})
    client = make_client(fake, store, exporter)
    await call(client)
    replay = await call(make_client(fake, store, exporter))  # fresh client, same store: crash recovery
    assert replay.cached and replay.output == PLAN
    assert fake.calls["planner"] == 1
    assert (await call(client, iteration=1)).output == PlanOutput(target_file="calc.py", steps=["other"])
    assert fake.calls["planner"] == 2


async def test_invalid_output_is_retried_once_with_the_error(
    store: SqliteStore, exporter: InMemorySpanExporter
) -> None:
    fake = FakeLLM({"planner": ["not json", PLAN]}, usage=LLMUsage(prompt_tokens=10, cost_usd=0.01))
    result = await call(make_client(fake, store, exporter))
    assert result.output == PLAN
    assert result.usage.prompt_tokens == 20 and result.usage.cost_usd == pytest.approx(0.02)
    assert "invalid" in fake.requests[1].user.lower()


async def test_second_invalid_output_raises(store: SqliteStore, exporter: InMemorySpanExporter) -> None:
    fake = FakeLLM({"planner": ['{"wrong": 1}', "[]"]}, usage=LLMUsage(cost_usd=0.01))
    with pytest.raises(StructuredOutputError) as err:
        await call(make_client(fake, store, exporter))
    assert err.value.usage.cost_usd == pytest.approx(0.02)
    assert fake.calls["planner"] == 2


async def test_budget_cap_stops_the_run(store: SqliteStore, exporter: InMemorySpanExporter) -> None:
    fake = FakeLLM({"planner": [PLAN, PLAN]}, usage=LLMUsage(cost_usd=1.0))
    client = make_client(fake, store, exporter, USD_EUR_RATE=1.0, MAX_COST_EUR_PER_RUN=0.5)
    with pytest.raises(BudgetExceededError):
        await call(client)
    assert await store.llm_cost_usd("run-1") == pytest.approx(1.0)  # the paid call is still accounted
    with pytest.raises(BudgetExceededError):
        await call(client, iteration=1)
    assert fake.calls["planner"] == 1  # no new call once over budget


async def test_each_llm_call_gets_a_span_through_the_sk_filter(
    store: SqliteStore, exporter: InMemorySpanExporter
) -> None:
    fake = FakeLLM({"coder": [PLAN]}, usage=LLMUsage(prompt_tokens=7, completion_tokens=3, cost_usd=0.5))
    await call(make_client(fake, store, exporter), service_id="coder")
    (span,) = exporter.get_finished_spans()
    assert span.name == "llm.coder"
    assert span.attributes == {
        "llm.provider": "mock",
        "llm.service_id": "coder",
        "llm.model": "mock/coder",
        "llm.tokens.prompt": 7,
        "llm.tokens.completion": 3,
        "llm.cost_usd": 0.5,
    }


async def test_backend_errors_are_not_hidden_by_the_kernel(
    store: SqliteStore, exporter: InMemorySpanExporter
) -> None:
    with pytest.raises(FakeScriptExhaustedError):
        await call(make_client(FakeLLM({}), store, exporter))


# --- kernel factory and OpenRouter backend -------------------------------------------------------------

LIVE = {
    "OPENROUTER_API_KEY": "sk-or-test",
    "PLANNER_MODEL": "vendor/planner",
    "CODER_MODEL": "vendor/coder",
    "SUMMARIZER_MODEL": "vendor/summarizer",
}


def test_build_kernel_requires_key_and_model_slugs() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY.*PLANNER_MODEL"):
        build_kernel(settings())


def test_build_kernel_registers_one_service_per_role() -> None:
    kernel = build_kernel(settings(**LIVE))
    for service_id in SERVICE_IDS:
        assert kernel.get_service(service_id).ai_model_id == f"vendor/{service_id}"


async def test_openrouter_backend_sends_structured_request_and_reads_cost() -> None:
    captured: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        captured.append(req)
        return httpx2.Response(
            200,
            json={
                "id": "gen-1",
                "object": "chat.completion",
                "created": 0,
                "model": "vendor/planner",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": PLAN.model_dump_json()},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16, "cost": 0.00042},
            },
        )

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    backend = OpenRouterBackend(build_kernel(settings(**LIVE), http_client=http))
    response = await backend.complete(request())

    (req,) = captured
    body = json.loads(req.content)
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["X-Title"] == "Mini-OpenCode-B1"
    assert req.headers["Authorization"] == "Bearer sk-or-test"
    assert body["model"] == "vendor/planner"
    assert body["response_format"]["type"] == "json_schema"
    assert body["usage"] == {"include": True}
    assert body["provider"] == {"require_parameters": True}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert PlanOutput.model_validate_json(response.content) == PLAN
    assert response.usage == LLMUsage(
        prompt_tokens=12, completion_tokens=4, cost_usd=0.00042, model_id="vendor/planner"
    )


async def test_provider_errors_become_a_short_resumable_error() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        message = "0 endpoints available.\nModel blocked by guardrail: 15 endpoints excluded"
        return httpx2.Response(404, json={"error": {"message": message, "code": 404}})

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    backend = OpenRouterBackend(build_kernel(settings(**LIVE), http_client=http))
    with pytest.raises(LLMProviderError) as err:
        await backend.complete(request())
    text = str(err.value)
    assert "planner" in text and "vendor/planner" in text and "HTTP 404" in text and "guardrail" in text
    assert "\n" not in text


class Collector:
    def __init__(self) -> None:
        self.starts: list[str] = []
        self.tokens: list[str] = []
        self.reasoning: list[str] = []

    def start(self, service_id: str) -> None:
        self.starts.append(service_id)

    def token(self, text: str, reasoning: bool = False) -> None:
        (self.reasoning if reasoning else self.tokens).append(text)


def sse(*chunks: dict[str, object]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


async def test_openrouter_backend_streams_tokens_reasoning_and_cost() -> None:
    content = PLAN.model_dump_json()
    base = {"id": "gen-2", "object": "chat.completion.chunk", "created": 0, "model": "vendor/planner"}

    def choice(**delta: object) -> dict[str, object]:
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}

    body = sse(
        choice(role="assistant", content="", reasoning="Thinking about calc.py"),
        choice(content=content[:10]),
        choice(content=content[10:]),
        {**base, "choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16,
                                          "cost": 0.0005}},
    )  # fmt: skip
    captured: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        captured.append(req)
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    backend = OpenRouterBackend(build_kernel(settings(**LIVE), http_client=http))
    stream = Collector()
    response = await backend.complete(replace(request(), stream=stream))

    assert json.loads(captured[0].content)["stream"] is True
    assert "".join(stream.tokens) == content and len(stream.tokens) == 2
    assert stream.reasoning == ["Thinking about calc.py"]
    assert PlanOutput.model_validate_json(response.content) == PLAN
    assert response.usage == LLMUsage(
        prompt_tokens=12, completion_tokens=4, cost_usd=0.0005, model_id="vendor/planner"
    )


async def test_structured_calls_stream_each_attempt_but_not_cache_hits(store: SqliteStore) -> None:
    fake = FakeLLM({"planner": ["not json", PLAN]})
    client = LLMClient(Kernel(), fake, store, settings())
    stream = Collector()
    await client.structured(
        run_id="r", step="plan", iteration=0, service_id="planner", system="s", user="u",
        output_type=PlanOutput, stream=stream,
    )  # fmt: skip
    assert stream.starts == ["planner", "planner"]  # the retry starts a fresh reply on screen
    assert "".join(stream.tokens) == "not json" + PLAN.model_dump_json()
    cached = Collector()
    await client.structured(
        run_id="r", step="plan", iteration=0, service_id="planner", system="s", user="u",
        output_type=PlanOutput, stream=cached,
    )  # fmt: skip
    assert cached.starts == [] and cached.tokens == []

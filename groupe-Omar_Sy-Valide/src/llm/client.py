"""Structured LLM calls: cache, one retry on invalid output, cost accounting, budget cap, tracing.

Every call goes through a Semantic Kernel native function, so the SK function-invocation filter
opens one `llm.<service_id>` span per call, in live and mock mode alike (SPEC §7).
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Generic, Protocol, TypeVar, cast

import openai
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from opentelemetry import trace
from opentelemetry.trace import Tracer
from pydantic import BaseModel, ValidationError
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.connectors.ai.open_ai import OpenAIChatPromptExecutionSettings
from semantic_kernel.contents import ChatHistory
from semantic_kernel.exceptions import KernelInvokeException, ServiceResponseException
from semantic_kernel.filters import FilterTypes, FunctionInvocationContext
from semantic_kernel.functions import KernelArguments, KernelFunction, kernel_function

from src.config import Settings
from src.observability.telemetry import record_llm_call
from src.storage.sqlite_store import SqliteStore

PLUGIN_NAME = "llm"
T = TypeVar("T", bound=BaseModel)


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model_id: str | None = None

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            model_id=other.model_id or self.model_id,
        )


@dataclass(frozen=True)
class ChatRequest:
    service_id: str
    system: str
    user: str
    output_type: type[BaseModel]
    stream: "TokenStream | None" = field(default=None, compare=False)  # live tokens, not part of the prompt


class TokenStream(Protocol):
    """Receives a reply while it is generated (the interactive agent shows it live)."""

    def start(self, service_id: str) -> None: ...

    def token(self, text: str, reasoning: bool = False) -> None: ...


@dataclass(frozen=True)
class ChatResponse:
    content: str
    usage: LLMUsage


class ChatBackend(Protocol):
    provider: str

    async def complete(self, request: ChatRequest) -> ChatResponse: ...


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    output: T
    usage: LLMUsage
    cached: bool


class LLMError(RuntimeError):
    def __init__(self, message: str, usage: LLMUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage or LLMUsage()  # cost already paid when the error is raised, for the step metrics


class StructuredOutputError(LLMError):
    pass


class BudgetExceededError(LLMError):
    pass


class LLMProviderError(RuntimeError):
    """The provider could not serve the call (blocked model, bad key, rate limit, outage).

    Deliberately not an LLMError: the engine treats it as a crash, so the run stays resumable at the
    current step once the cause (often the model slug in .env) is fixed.
    """


def _provider_reason(exc: BaseException) -> str:
    """One line from the OpenAI-compatible error SK wraps, e.g. 'HTTP 404: Model blocked by guardrail'."""
    for candidate in (exc, exc.__cause__):
        if isinstance(candidate, openai.APIStatusError):
            body: object = candidate.body
            detail: object = cast(dict[str, object], body).get("message") if isinstance(body, dict) else None
            text = str(detail) if detail else candidate.message
            return f"HTTP {candidate.status_code}: {' '.join(text.split())[:400]}"
        if isinstance(candidate, openai.APIConnectionError):
            return f"connection error: {candidate}"
    return " ".join(str(exc).split())[:400]


def prompt_hash(request: ChatRequest) -> str:
    key = [request.service_id, request.system, request.user, request.output_type.__name__]
    return hashlib.sha256(json.dumps(key).encode("utf-8")).hexdigest()


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.removesuffix("```").strip()
    return text


class OpenRouterBackend:
    """Calls the SK chat service registered under the request's service_id (see kernel_factory)."""

    provider = "openrouter"

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel

    async def complete(self, request: ChatRequest) -> ChatResponse:
        service = self.kernel.get_service(request.service_id, type=ChatCompletionClientBase)
        history = ChatHistory()
        history.add_system_message(request.system)
        history.add_user_message(request.user)
        settings = OpenAIChatPromptExecutionSettings(
            service_id=request.service_id,
            response_format=request.output_type,
            temperature=0.0,
            extra_body={
                "usage": {"include": True},  # OpenRouter usage accounting: returns usage.cost
                "provider": {"require_parameters": True},  # only providers supporting json_schema
            },
        )
        try:
            if request.stream is not None:
                return await self._stream(service, history, settings, request.stream)
            message = await service.get_chat_message_content(history, settings)
        except (ServiceResponseException, openai.APIError) as exc:
            reason = _provider_reason(exc)
            raise LLMProviderError(f"{request.service_id} ({service.ai_model_id}): {reason}") from exc
        if message is None:
            raise LLMError(f"{request.service_id}: empty response")
        return ChatResponse(
            content=message.content, usage=_usage_from(message.inner_content, service.ai_model_id)
        )

    @staticmethod
    async def _stream(
        service: ChatCompletionClientBase,
        history: ChatHistory,
        settings: OpenAIChatPromptExecutionSettings,
        stream: TokenStream,
    ) -> ChatResponse:
        """Same request, streamed: tokens (and reasoning, for thinking models) go to `stream` as they come;
        the usage and cost arrive in the last chunk."""
        parts: list[str] = []
        usage = LLMUsage(model_id=service.ai_model_id)
        async for chunk in service.get_streaming_chat_message_content(history, settings):
            if chunk is None:
                continue
            inner = chunk.inner_content
            if isinstance(inner, ChatCompletionChunk):
                if inner.usage is not None:
                    usage = _usage_from(inner, service.ai_model_id)
                for choice in inner.choices:
                    reasoning = (choice.delta.model_extra or {}).get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        stream.token(reasoning, reasoning=True)
            text = str(chunk.content or "")
            if text:
                parts.append(text)
                stream.token(text)
        if not parts:
            raise LLMError(f"{settings.service_id}: empty response")
        return ChatResponse(content="".join(parts), usage=usage)


def _usage_from(inner: object, fallback_model: str) -> LLMUsage:
    if not isinstance(inner, ChatCompletion | ChatCompletionChunk) or inner.usage is None:
        return LLMUsage(model_id=fallback_model)
    extra = inner.usage.model_extra or {}
    return LLMUsage(
        prompt_tokens=inner.usage.prompt_tokens,
        completion_tokens=inner.usage.completion_tokens,
        cost_usd=float(extra.get("cost") or 0.0),
        model_id=inner.model or fallback_model,
    )


class LLMClient:
    def __init__(
        self,
        kernel: Kernel,
        backend: ChatBackend,
        store: SqliteStore,
        settings: Settings,
        tracer: Tracer | None = None,
    ) -> None:
        self.kernel = kernel
        self.backend = backend
        self.store = store
        self.settings = settings
        self.tracer = tracer or trace.get_tracer(__name__)
        self._chat_function = KernelFunction.from_method(self._chat, plugin_name=PLUGIN_NAME)
        kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, self._tracing_filter)  # pyright: ignore[reportUnknownMemberType]

    async def structured(
        self,
        *,
        run_id: str,
        step: str,
        iteration: int,
        service_id: str,
        system: str,
        user: str,
        output_type: type[T],
        stream: TokenStream | None = None,
    ) -> LLMResult[T]:
        request = ChatRequest(service_id, system, user, output_type, stream)
        key = (run_id, step, iteration, prompt_hash(request))
        cached = await self.store.get_llm_cache(*key)
        if cached is not None:
            return LLMResult(
                output_type.model_validate_json(cached.response_json),
                LLMUsage.model_validate_json(cached.usage_json),
                cached=True,
            )

        await self._check_budget(run_id, LLMUsage())
        usage, error = LLMUsage(), ""
        for attempt in range(2):
            attempt_request = (
                request
                if attempt == 0
                else replace(
                    request,
                    user=f"{user}\n\nYour previous reply was invalid: {error}\n"
                    "Reply with a single JSON object that matches the schema.",
                )
            )
            if stream is not None:
                stream.start(service_id)  # a retry starts a fresh reply on screen
            response = await self._invoke(attempt_request)
            usage = usage + response.usage
            try:
                output = output_type.model_validate_json(_strip_code_fence(response.content))
            except ValidationError as exc:
                error = str(exc)[:1000]
                continue
            await self.store.put_llm_cache(
                *key, response_json=output.model_dump_json(), usage_json=usage.model_dump_json()
            )
            await self._check_budget(run_id, usage)
            return LLMResult(output, usage, cached=False)
        raise StructuredOutputError(
            f"{service_id}: invalid structured output after one retry: {error}", usage
        )

    async def _check_budget(self, run_id: str, usage: LLMUsage) -> None:
        spent_eur = self.settings.usd_to_eur(await self.store.llm_cost_usd(run_id))
        if spent_eur > self.settings.MAX_COST_EUR_PER_RUN:
            raise BudgetExceededError(
                f"run {run_id} spent {spent_eur:.4f} EUR, "
                f"above MAX_COST_EUR_PER_RUN={self.settings.MAX_COST_EUR_PER_RUN}",
                usage,
            )

    async def _invoke(self, request: ChatRequest) -> ChatResponse:
        try:
            result = await self.kernel.invoke(self._chat_function, KernelArguments(request=request))
        except (
            KernelInvokeException
        ) as exc:  # SK wraps errors raised inside the function: re-raise the original
            if isinstance(exc.__cause__, Exception):
                raise exc.__cause__ from None
            raise
        if result is None or not isinstance(result.value, ChatResponse):
            raise LLMError(f"{request.service_id}: backend returned no response")
        return result.value

    @kernel_function(name="chat")
    async def _chat(
        self, request: Any
    ) -> ChatResponse:  # Any: SK only passes Any-typed objects through as-is
        if not isinstance(request, ChatRequest):
            raise TypeError(f"expected ChatRequest, got {type(request).__name__}")
        return await self.backend.complete(request)

    async def _tracing_filter(
        self,
        context: FunctionInvocationContext,
        next: Callable[[FunctionInvocationContext], Awaitable[None]],  # name required by SK
    ) -> None:
        arguments = context.arguments  # KernelArguments is an untyped dict subclass
        request: object = arguments.get("request")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if context.function.plugin_name != PLUGIN_NAME or not isinstance(request, ChatRequest):
            await next(context)
            return
        with self.tracer.start_as_current_span(f"llm.{request.service_id}") as span:
            await next(context)
            value = context.result.value if context.result is not None else None
            if isinstance(value, ChatResponse):
                record_llm_call(
                    span,
                    provider=self.backend.provider,
                    service_id=request.service_id,
                    model=value.usage.model_id or "",
                    prompt_tokens=value.usage.prompt_tokens,
                    completion_tokens=value.usage.completion_tokens,
                    cost_usd=value.usage.cost_usd,
                )

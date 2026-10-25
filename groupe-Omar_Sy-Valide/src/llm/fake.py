"""Scripted fake LLM for `--mock-llm` and tests: deterministic responses, call counter, zero network."""

import json
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeAlias, cast

from pydantic import BaseModel

from src.llm.client import ChatRequest, ChatResponse, LLMUsage
from src.models.state import ErrorSummary

ScriptItem: TypeAlias = str | BaseModel | dict[str, Any]


class FakeScriptExhaustedError(RuntimeError):
    pass


def _to_content(item: ScriptItem) -> str:
    if isinstance(item, BaseModel):
        return item.model_dump_json()
    if isinstance(item, dict):
        return json.dumps(item)
    return item  # raw string, possibly invalid JSON on purpose


class FakeLLM:
    """Returns the next scripted response for the request's service_id.

    Without a script entry, the summarizer answers with a deterministic truncation of its input;
    any other service fails loudly, so a test never silently gets an unexpected response.
    """

    provider = "mock"

    def __init__(
        self,
        script: Mapping[str, Sequence[ScriptItem]] | None = None,
        *,
        usage: LLMUsage | None = None,
        summary_chars: int = 2000,
    ) -> None:
        self._queues = {sid: deque(_to_content(i) for i in items) for sid, items in (script or {}).items()}
        self.usage = usage or LLMUsage(prompt_tokens=100, completion_tokens=50)
        self.summary_chars = summary_chars
        self.calls: Counter[str] = Counter()
        self.requests: list[ChatRequest] = []

    @classmethod
    def from_file(cls, path: Path, **kwargs: Any) -> "FakeLLM":
        """Load a script `{"planner": [...], "coder": [...]}` whose items are JSON objects or raw strings."""
        data: object = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(
            isinstance(v, list) for v in cast(dict[str, object], data).values()
        ):
            raise ValueError(f"{path}: expected an object mapping service ids to lists of responses")
        return cls(cast(dict[str, list[ScriptItem]], data), **kwargs)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls[request.service_id] += 1
        self.requests.append(request)
        queue = self._queues.get(request.service_id)
        if queue:
            content = queue.popleft()
        elif request.service_id == "summarizer":
            content = ErrorSummary(summary=request.user[: self.summary_chars]).model_dump_json()
        else:
            raise FakeScriptExhaustedError(f"no scripted response left for '{request.service_id}'")
        if request.stream is not None:  # same contract as the live backend: the reply in pieces
            for start in range(0, len(content), 16):
                request.stream.token(content[start : start + 16])
        return ChatResponse(content, self.usage.model_copy(update={"model_id": f"mock/{request.service_id}"}))

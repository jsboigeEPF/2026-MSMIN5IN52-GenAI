"""Streaming NDJSON client. Cancellation exits the response context immediately."""

import json
from collections.abc import AsyncIterator

import httpx

from voiceagent.config import LlmConfig


class Ollama:
    def __init__(self, config: LlmConfig, seed: int, transport=None):
        self.config = config
        self.seed = seed
        self.last_eval_count = None
        self._digest_verified = False
        self._client = httpx.AsyncClient(
            base_url=config.url,
            timeout=httpx.Timeout(config.timeout_s, connect=5.0),
            transport=transport,
        )

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        if self.config.expected_digest and not self._digest_verified:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            models = response.json()["models"]
            actual = next(
                (item["digest"] for item in models if item["name"] == self.config.model), None
            )
            if actual != self.config.expected_digest:
                raise RuntimeError(
                    "Le digest Ollama a changé depuis prepare. Refiger les modèles."
                )
            self._digest_verified = True
        self.last_eval_count = None
        body = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {
                "seed": self.seed,
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
                "num_predict": self.config.max_tokens,
            },
        }
        completed = False
        async with self._client.stream("POST", "/api/chat", json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if "error" in event:
                    raise RuntimeError(f"Ollama : {event['error']}")
                content = event.get("message", {}).get("content", "")
                if content:
                    yield content
                if event.get("done"):
                    self.last_eval_count = event.get("eval_count")
                    completed = True
                    break
        if not completed:
            raise RuntimeError("Flux Ollama tronqué avant done=true")

    async def close(self) -> None:
        await self._client.aclose()

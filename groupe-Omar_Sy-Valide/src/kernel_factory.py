"""Semantic Kernel with one OpenRouter chat service per role (SPEC §2).

Model slugs come only from settings, never from code.
"""

import httpx2
from openai import AsyncOpenAI
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion

from src.config import Settings

SERVICE_IDS = ("planner", "coder", "summarizer")
OPENROUTER_HEADERS = {"X-Title": "Mini-OpenCode-B1"}


def service_models(settings: Settings) -> dict[str, str]:
    return {
        "planner": settings.PLANNER_MODEL,
        "coder": settings.CODER_MODEL,
        "summarizer": settings.SUMMARIZER_MODEL,
    }


def build_kernel(settings: Settings, http_client: httpx2.AsyncClient | None = None) -> Kernel:
    """Live-mode kernel. `http_client` lets tests intercept HTTP without any network call."""
    api_key = settings.OPENROUTER_API_KEY.get_secret_value() if settings.OPENROUTER_API_KEY else ""
    models = service_models(settings)
    missing = ([] if api_key else ["OPENROUTER_API_KEY"]) + [
        f"{service_id.upper()}_MODEL" for service_id, model in models.items() if not model
    ]
    if missing:
        raise ValueError(f"live mode needs these settings in .env: {', '.join(missing)}")

    client = AsyncOpenAI(
        base_url=settings.OPENROUTER_BASE_URL,
        api_key=api_key,
        default_headers=OPENROUTER_HEADERS,
        http_client=http_client,
    )
    kernel = Kernel()
    for service_id, model in models.items():
        kernel.add_service(
            OpenAIChatCompletion(service_id=service_id, ai_model_id=model, async_client=client)
        )
    return kernel

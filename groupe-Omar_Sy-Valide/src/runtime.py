"""Builds a ProcessRunner for the CLI and the dashboard (same wiring for both)."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from semantic_kernel import Kernel

from src.clock import SystemClock
from src.config import Settings
from src.kernel_factory import build_kernel
from src.llm.client import ChatBackend, LLMClient, OpenRouterBackend
from src.llm.fake import FakeLLM
from src.observability.telemetry import build_tracer_provider
from src.process.engine import ProcessRunner
from src.storage.sqlite_store import SqliteStore
from src.tools.sandbox import SandboxRunner


@asynccontextmanager
async def open_runner(
    db: Path, *, drive: bool, mock: bool = False, script: Path | None = None, trace_console: bool = False
) -> AsyncGenerator[ProcessRunner]:
    """A runner over `db`. Only callers that execute steps (`drive`) get an LLM client and a sandbox,
    so reading and sweeping never need an API key or Docker."""
    settings = Settings()
    provider: TracerProvider = build_tracer_provider(
        console=trace_console, otlp=bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    )
    tracer = provider.get_tracer("mini-opencode")
    try:
        async with SqliteStore(db) as store:
            llm, sandbox = None, None
            if drive:
                backend: ChatBackend
                if mock:
                    kernel = Kernel()
                    backend = FakeLLM.from_file(script) if script else FakeLLM()
                else:
                    kernel = build_kernel(settings)
                    backend = OpenRouterBackend(kernel)
                llm = LLMClient(kernel, backend, store, settings, tracer)
                sandbox = SandboxRunner(
                    settings.SANDBOX_MODE, settings.SANDBOX_TIMEOUT_SEC, settings.SANDBOX_IMAGE
                )
            yield ProcessRunner(
                store=store, llm=llm, sandbox=sandbox, settings=settings, clock=SystemClock(), tracer=tracer
            )
    finally:
        provider.shutdown()

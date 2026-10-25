"""Command-line interface: start, status, decide, resume, sweep, report (plus runs and credits).

    python -m src.cli start --mock-llm --mock-script script.json --repo ../demo --file calc.py --prompt "..."
    python -m src.cli status <run_id>
    python -m src.cli decide <run_id> --action approve --actor alice

`--mock-llm` uses the scripted fake LLM (zero network).
Without it, the OpenRouter models configured in `.env` are used.
"""

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

import httpx2
import typer
from opentelemetry.sdk.trace import TracerProvider
from semantic_kernel import Kernel

from src.clock import SystemClock
from src.config import Settings
from src.kernel_factory import build_kernel
from src.llm.client import ChatBackend, LLMClient, OpenRouterBackend
from src.llm.fake import FakeLLM, FakeScriptExhaustedError
from src.models.state import AgentProcessState
from src.observability.telemetry import build_tracer_provider, generate_evaluation_report
from src.process.engine import DecisionRefusedError, DecisionRequest, ProcessRunner, StartRequest
from src.storage.sqlite_store import SqliteStore
from src.tools.git_ops import GitError
from src.tools.sandbox import SandboxRunner, SandboxUnavailableError

app = typer.Typer(help="Mini-OpenCode: deterministic coding agent with human approval.", no_args_is_help=True)

Db = Annotated[Path, typer.Option("--db", help="SQLite database holding runs, journal and metrics.")]
Mock = Annotated[bool, typer.Option("--mock-llm", help="Use the scripted fake LLM (no network).")]
Script = Annotated[Path | None, typer.Option("--mock-script", help="JSON script for --mock-llm.")]
Trace = Annotated[bool, typer.Option("--trace", help="Print OpenTelemetry spans to the console.")]
DEFAULT_DB = Path("mini-opencode.db")
T = TypeVar("T")


def _run(coro: Coroutine[Any, Any, T]) -> T:
    """Run a command; expected errors become a one-line message and exit code 1."""
    try:
        return asyncio.run(coro)
    except (
        DecisionRefusedError,
        LookupError,
        SandboxUnavailableError,
        FakeScriptExhaustedError,
        GitError,
        ValueError,
    ) as exc:
        typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from None


@asynccontextmanager
async def _runner(
    db: Path, *, drive: bool, mock: bool = False, script: Path | None = None, trace_console: bool = False
) -> AsyncGenerator[ProcessRunner]:
    """A runner over `db`. Only commands that execute steps (`drive`) get an LLM client and a sandbox."""
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


async def _describe(runner: ProcessRunner, state: AgentProcessState) -> str:
    settings = Settings()
    cost = await runner.store.llm_cost_usd(state.run_id)
    lines = [
        f"run {state.run_id}  [{state.status.value}]  step={state.current_step}",
        f"scenario: {state.scenario or '-'}",
        f"target: {state.target_repo_path} :: {state.target_file} (base {state.base_branch})",
        f"iterations: loop {state.loop_iteration}, fix {state.fix_iteration}, "
        f"revision {state.revision_iteration}",
        f"cost so far: ${cost:.6f} ({settings.usd_to_eur(cost):.6f} EUR)",
    ]
    if state.approval_deadline is not None and state.status.value == "AWAITING_APPROVAL":
        lines.append(f"approval deadline: {state.approval_deadline.isoformat()}")
    if state.execution_plan:
        lines.append("plan:")
        lines += [f"  {i}. {step}" for i, step in enumerate(state.execution_plan, 1)]
    if state.verification is not None:
        v = state.verification
        verdict = {True: "PASSED", False: "FAILED"}
        lines.append(f"verification: pytest {verdict[v.pytest_passed]}, ruff {verdict[v.linter_passed]}")
        if v.error_summary:
            lines.append("  summary: " + v.error_summary.replace("\n", "\n  "))
    if state.unified_diff:
        lines += ["diff:", state.unified_diff.rstrip("\n")]
    if state.terminal_reason:
        lines.append(f"terminal reason: {state.terminal_reason}")
    if state.compensation is not None:
        c = state.compensation
        lines.append(
            f"compensation: {c.action} ({'done' if c.done else 'NOT done'}) {c.detail or ''}".rstrip()
        )
    if state.error_message:
        lines.append(f"error: {state.error_message}")
    return "\n".join(lines)


@app.command()
def start(
    repo: Annotated[Path, typer.Option(help="Target git repository (its base branch must be checked out).")],
    file: Annotated[str, typer.Option(help="File to change, relative to the repository root.")],
    prompt: Annotated[str, typer.Option(help="What the agent should do.")],
    base: Annotated[str, typer.Option(help="Base branch.")] = "main",
    scenario: Annotated[str | None, typer.Option(help="Label used by the evaluation report.")] = None,
    run_id: Annotated[str | None, typer.Option(help="Run id (default: random).")] = None,
    db: Db = DEFAULT_DB,
    mock_llm: Mock = False,
    mock_script: Script = None,
    trace: Trace = False,
) -> None:
    """Start a run: plan, code and verify, then stop at human approval."""

    async def go() -> str:
        async with _runner(db, drive=True, mock=mock_llm, script=mock_script, trace_console=trace) as runner:
            request = StartRequest(
                target_repo_path=str(repo),
                base_branch=base,
                target_file=file,
                user_prompt=prompt,
                scenario=scenario,
                run_id=run_id,
            )
            new_id = await runner.start(request)
            return await _describe(runner, await runner.status(new_id))

    typer.echo(_run(go()))


@app.command()
def status(run_id: str, db: Db = DEFAULT_DB) -> None:
    """Show a run: plan, diff, verification, counters, cost (expires overdue approvals first)."""

    async def go() -> str:
        async with _runner(db, drive=False) as runner:
            return await _describe(runner, await runner.status(run_id))

    typer.echo(_run(go()))


@app.command()
def decide(
    run_id: str,
    action: Annotated[Literal["approve", "revise", "reject"], typer.Option(help="The decision.")],
    actor: Annotated[str, typer.Option(help="Who decides (stored in the audit log).")],
    comment: Annotated[str | None, typer.Option(help="Required for revise.")] = None,
    db: Db = DEFAULT_DB,
    mock_llm: Mock = False,
    mock_script: Script = None,
    trace: Trace = False,
) -> None:
    """Record a human decision and continue the run."""

    async def go() -> str:
        async with _runner(db, drive=True, mock=mock_llm, script=mock_script, trace_console=trace) as runner:
            request = DecisionRequest(action=action, actor=actor, comment=comment)
            return await _describe(runner, await runner.decide(run_id, request))

    typer.echo(_run(go()))


@app.command()
def resume(
    run_id: str,
    db: Db = DEFAULT_DB,
    mock_llm: Mock = False,
    mock_script: Script = None,
    trace: Trace = False,
) -> None:
    """Resume a run interrupted by a crash, from the step that did not complete."""

    async def go() -> str:
        async with _runner(db, drive=True, mock=mock_llm, script=mock_script, trace_console=trace) as runner:
            return await _describe(runner, await runner.resume(run_id))

    typer.echo(_run(go()))


@app.command()
def sweep(db: Db = DEFAULT_DB) -> None:
    """Expire every run whose approval deadline has passed."""

    async def go() -> list[str]:
        async with _runner(db, drive=False) as runner:
            return await runner.sweep_expired()

    expired = _run(go())
    typer.echo("\n".join(f"expired: {r}" for r in expired) if expired else "no run expired")


@app.command()
def runs(db: Db = DEFAULT_DB) -> None:
    """List runs with their status."""

    async def go() -> list[AgentProcessState]:
        async with _runner(db, drive=False) as runner:
            await runner.sweep_expired()
            return await runner.store.list_runs()

    for s in _run(go()):
        reason = f" ({s.terminal_reason})" if s.terminal_reason else ""
        typer.echo(
            f"{s.run_id}\t{s.status.value}{reason}\tstep={s.current_step}\tscenario={s.scenario or '-'}"
        )


@app.command()
def report(db: Db = DEFAULT_DB) -> None:
    """Evaluation report: completion rate, compute/wait time, iterations, cost vs budget (JSON)."""
    typer.echo(_run(generate_evaluation_report(db)).model_dump_json(indent=2))


async def fetch_key_info(settings: Settings, http_client: httpx2.AsyncClient | None = None) -> dict[str, Any]:
    """Usage and limit of the configured OpenRouter key (GET /key). The key itself is never returned."""
    if settings.OPENROUTER_API_KEY is None:
        raise ValueError("OPENROUTER_API_KEY is not set in .env")
    headers = {"Authorization": f"Bearer {settings.OPENROUTER_API_KEY.get_secret_value()}"}
    async with http_client or httpx2.AsyncClient(timeout=20) as client:
        response = await client.get(f"{settings.OPENROUTER_BASE_URL}/key", headers=headers)
    if response.status_code != 200:
        raise ValueError(f"OpenRouter /key returned HTTP {response.status_code}")
    data: dict[str, Any] = response.json().get("data", {})
    return data


@app.command()
def credits() -> None:
    """Show the OpenRouter key's usage and limit (no LLM call, no cost)."""
    info = _run(fetch_key_info(Settings()))
    typer.echo(json.dumps(info, indent=2))


if __name__ == "__main__":
    app()

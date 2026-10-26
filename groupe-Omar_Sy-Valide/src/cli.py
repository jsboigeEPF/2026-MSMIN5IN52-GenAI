"""Command-line interface: start, status, decide, resume, undo, sweep, report (plus runs and credits).

The interactive agent is `python omar.py`; this CLI drives the same engine one command at a time.

    python -m src.cli start --mock-llm --mock-script script.json --repo ../demo --prompt "..."
    python -m src.cli status <run_id>
    python -m src.cli decide <run_id> --action approve --actor alice   # acts on the pending gate

`--mock-llm` uses the scripted fake LLM (zero network).
Without it, the OpenRouter models configured in `.env` are used.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Coroutine
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

import httpx2
import typer

from src.config import DEFAULT_DB, Settings
from src.llm.client import LLMProviderError
from src.llm.fake import FakeScriptExhaustedError
from src.models.state import AgentProcessState
from src.observability.telemetry import generate_evaluation_report
from src.process.engine import DecisionRefusedError, DecisionRequest, ProcessRunner, StartRequest
from src.process.steps import HumanApprovalStep
from src.runtime import open_runner
from src.safe_text import printable
from src.tools.git_ops import GitError
from src.tools.sandbox import SandboxUnavailableError

app = typer.Typer(help="Mini-OpenCode: deterministic coding agent with human approval.", no_args_is_help=True)

Db = Annotated[Path, typer.Option("--db", help="SQLite database holding runs, journal and metrics.")]
Mock = Annotated[bool, typer.Option("--mock-llm", help="Use the scripted fake LLM (no network).")]
Script = Annotated[Path | None, typer.Option("--mock-script", help="JSON script for --mock-llm.")]
Trace = Annotated[bool, typer.Option("--trace", help="Print OpenTelemetry spans to the console.")]
T = TypeVar("T")


@app.callback()
def _configure_logging() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # Semantic Kernel logs every failed call with the full provider payload; the CLI reports it in one line.
    logging.getLogger("semantic_kernel").setLevel(logging.CRITICAL)


def _run(coro: Coroutine[Any, Any, T], run_id: str | None = None) -> T:
    """Run a command; expected errors become a one-line message and exit code 1."""
    try:
        return asyncio.run(coro)
    except LLMProviderError as exc:
        typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
        if run_id:
            typer.echo(
                "the run is saved at its last completed step; fix the cause (e.g. the model in .env), then: "
                f"python -m src.cli resume {run_id}",
                err=True,
            )
        raise typer.Exit(1) from None
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


async def _describe(runner: ProcessRunner, state: AgentProcessState) -> str:
    settings = Settings()
    cost = await runner.store.llm_cost_usd(state.run_id)
    lines = [
        f"run {state.run_id}  [{state.status.value}]  step={state.current_step}",
        f"scenario: {state.scenario or '-'}",
        f"target: {state.target_repo_path} :: {state.target_file or '(planner picks)'}",
        f"iterations: loop {state.loop_iteration}, fix {state.fix_iteration}, "
        f"revision {state.revision_iteration}",
        f"cost so far: ${cost:.6f} ({settings.usd_to_eur(cost):.6f} EUR)",
    ]
    gate = HumanApprovalStep.gate(state)
    if gate is not None:
        lines.append(f"awaiting the {gate} gate: approve, revise (with a comment) or reject")
        if HumanApprovalStep.write_anyway_allowed(state, runner.limits):
            lines.append(
                f"WARNING: tests still fail after {runner.limits.max_fix} automatic fixes: revise, reject, "
                "or --action write_anyway to write this failing code as is"
            )
        if state.approval_deadline is not None:
            lines.append(f"approval deadline: {state.approval_deadline.isoformat()}")
    if state.execution_plan:
        lines.append("plan:")
        lines += [f"  {i}. {step}" for i, step in enumerate(state.execution_plan, 1)]
    if state.verification is not None:
        v = state.verification
        verdict = {True: "PASSED", False: "FAILED"}
        tests, lint = (
            f"{v.tests_label} {verdict[v.pytest_passed]}",
            f"{v.lint_label} {verdict[v.linter_passed]}",
        )
        lines.append(f"verification: {tests}, {lint}")
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
    return printable("\n".join(lines))  # LLM and repository text: no raw control character on the terminal


@app.command()
def start(
    repo: Annotated[
        Path, typer.Option(help="Target git repository (the file is written in its working tree).")
    ],
    prompt: Annotated[str, typer.Option(help="What the agent should do.")],
    file: Annotated[
        str | None, typer.Option(help="File to change, relative to the repository root (default: AI picks).")
    ] = None,
    base: Annotated[str | None, typer.Option(help="Branch to start from (default: checked out).")] = None,
    scenario: Annotated[str | None, typer.Option(help="Label used by the evaluation report.")] = None,
    run_id: Annotated[str | None, typer.Option(help="Run id (default: random).")] = None,
    db: Db = DEFAULT_DB,
    mock_llm: Mock = False,
    mock_script: Script = None,
    trace: Trace = False,
) -> None:
    """Start a run: plan, then stop at the plan gate."""
    new_id = run_id or uuid.uuid4().hex[:12]

    async def go() -> str:
        opened = open_runner(db, drive=True, mock=mock_llm, script=mock_script, trace_console=trace)
        async with opened as runner:
            request = StartRequest(
                target_repo_path=str(repo),
                base_branch=base,
                target_file=file,
                user_prompt=prompt,
                scenario=scenario,
                run_id=new_id,
            )
            await runner.start(request)
            return await _describe(runner, await runner.status(new_id))

    typer.echo(_run(go(), new_id))


@app.command()
def status(run_id: str, db: Db = DEFAULT_DB) -> None:
    """Show a run: plan, diff, verification, counters, cost (expires overdue approvals first)."""

    async def go() -> str:
        async with open_runner(db, drive=False) as runner:
            return await _describe(runner, await runner.status(run_id))

    typer.echo(_run(go()))


@app.command()
def decide(
    run_id: str,
    action: Annotated[
        Literal["approve", "revise", "reject", "write_anyway"],
        typer.Option(help="The decision (write_anyway: only once the automatic fixes ran out)."),
    ],
    actor: Annotated[str, typer.Option(help="Who decides (stored in the audit log).")],
    comment: Annotated[str | None, typer.Option(help="Required for revise.")] = None,
    db: Db = DEFAULT_DB,
    mock_llm: Mock = False,
    mock_script: Script = None,
    trace: Trace = False,
) -> None:
    """Record a human decision on the pending gate (plan or write) and continue the run."""

    async def go() -> str:
        opened = open_runner(db, drive=True, mock=mock_llm, script=mock_script, trace_console=trace)
        async with opened as runner:
            request = DecisionRequest(action=action, actor=actor, comment=comment)
            return await _describe(runner, await runner.decide(run_id, request))

    typer.echo(_run(go(), run_id))


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
        opened = open_runner(db, drive=True, mock=mock_llm, script=mock_script, trace_console=trace)
        async with opened as runner:
            return await _describe(runner, await runner.resume(run_id))

    typer.echo(_run(go(), run_id))


@app.command()
def undo(run_id: str, db: Db = DEFAULT_DB) -> None:
    """Restore the file a completed run wrote (refused if it was modified since)."""

    async def go() -> str:
        async with open_runner(db, drive=False) as runner:
            state = await runner.undo(run_id)
            return f"restored {state.target_file} in {state.target_repo_path}"

    typer.echo(_run(go()))


@app.command()
def sweep(db: Db = DEFAULT_DB) -> None:
    """Expire every run whose approval deadline has passed."""

    async def go() -> list[str]:
        async with open_runner(db, drive=False) as runner:
            return await runner.sweep_expired()

    expired = _run(go())
    typer.echo("\n".join(f"expired: {r}" for r in expired) if expired else "no run expired")


@app.command()
def runs(db: Db = DEFAULT_DB) -> None:
    """List runs with their status."""

    async def go() -> list[AgentProcessState]:
        async with open_runner(db, drive=False) as runner:
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

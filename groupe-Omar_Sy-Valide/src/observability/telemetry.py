"""OpenTelemetry tracing helpers and the evaluation report (SPEC §7)."""

import math
from collections import defaultdict
from collections.abc import Collection, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import Span, Tracer
from pydantic import BaseModel

from src.config import Settings
from src.models.state import ApprovalDecision, ProcessStatus, StepMetrics, VerificationResult
from src.storage.sqlite_store import SqliteStore

# Span attribute names (SPEC §7).
RUN_ID = "process.run_id"
STEP_NAME = "process.step.name"
ITERATION = "process.iteration"
STEP_COMPUTE_MS = "process.step.compute_ms"
STEP_WAIT_MS = "process.step.wait_ms"
LLM_PROVIDER = "llm.provider"
LLM_SERVICE_ID = "llm.service_id"
LLM_MODEL = "llm.model"
LLM_TOKENS_PROMPT = "llm.tokens.prompt"
LLM_TOKENS_COMPLETION = "llm.tokens.completion"
LLM_COST_USD = "llm.cost_usd"
VERIFICATION_PYTEST_PASSED = "verification.pytest_passed"
VERIFICATION_LINTER_PASSED = "verification.linter_passed"
APPROVAL_GATE = "approval.gate"
APPROVAL_ACTION = "approval.action"
APPROVAL_ACTOR = "approval.actor"


def build_tracer_provider(
    *,
    service_name: str = "mini-opencode",
    console: bool = False,
    otlp: bool = False,
    exporters: Sequence[SpanExporter] = (),
) -> TracerProvider:
    """OTLP endpoint and headers come from the standard OTEL_EXPORTER_OTLP_* environment variables."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    selected: list[SpanExporter] = list(exporters)
    if console:
        selected.append(ConsoleSpanExporter())
    if otlp:
        selected.append(OTLPSpanExporter())
    for exporter in selected:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


@contextmanager
def run_span(tracer: Tracer, run_id: str) -> Generator[Span]:
    with tracer.start_as_current_span("process.run", attributes={RUN_ID: run_id}) as span:
        yield span


@contextmanager
def step_span(tracer: Tracer, run_id: str, step: str, iteration: int) -> Generator[Span]:
    attributes = {RUN_ID: run_id, STEP_NAME: step, ITERATION: iteration}
    with tracer.start_as_current_span(f"step.{step}", attributes=attributes) as span:
        yield span


def record_step_metrics(span: Span, metrics: StepMetrics) -> None:
    span.set_attribute(STEP_COMPUTE_MS, metrics.compute_ms)
    span.set_attribute(STEP_WAIT_MS, metrics.wait_ms)


def record_verification(span: Span, result: VerificationResult) -> None:
    span.set_attribute(VERIFICATION_PYTEST_PASSED, result.pytest_passed)
    span.set_attribute(VERIFICATION_LINTER_PASSED, result.linter_passed)


def record_approval(span: Span, decision: ApprovalDecision) -> None:
    span.set_attribute(APPROVAL_GATE, decision.gate)
    span.set_attribute(APPROVAL_ACTION, decision.action)
    span.set_attribute(APPROVAL_ACTOR, decision.actor)


def record_llm_call(
    span: Span,
    *,
    provider: str,
    service_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> None:
    span.set_attribute(LLM_PROVIDER, provider)
    span.set_attribute(LLM_SERVICE_ID, service_id)
    span.set_attribute(LLM_MODEL, model)
    span.set_attribute(LLM_TOKENS_PROMPT, prompt_tokens)
    span.set_attribute(LLM_TOKENS_COMPLETION, completion_tokens)
    span.set_attribute(LLM_COST_USD, cost_usd)


# --- evaluation report -------------------------------------------------------------------------------


class Stats(BaseModel):
    count: int
    mean_ms: float
    p95_ms: float


class ScenarioCompletion(BaseModel):
    runs: int
    completed: int
    rate: float


class EvaluationReport(BaseModel):
    runs: int
    completion_by_scenario: dict[str, ScenarioCompletion]
    compute_by_step: dict[str, Stats]
    compute_by_model: dict[str, Stats]
    human_wait: Stats
    avg_fix_iterations: float
    avg_revision_iterations: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cost_eur: float
    budget_eur: float
    budget_used_pct: float


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty sequence."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _stats(values: Sequence[float]) -> Stats:
    mean = sum(values) / len(values) if values else 0.0
    return Stats(count=len(values), mean_ms=mean, p95_ms=percentile(values, 95))


async def generate_evaluation_report(
    db_path: str | Path,
    *,
    usd_eur_rate: float | None = None,
    budget_eur: float | None = None,
    approval_steps: Collection[str] = ("plan_approval", "write_approval"),
) -> EvaluationReport:
    """Completion rate per scenario, compute time per step/model (mean, P95), human wait time,
    average fix/revision iterations, tokens and cost against the budget. Runs without a scenario
    label are grouped under "unlabeled"."""
    if usd_eur_rate is None or budget_eur is None:
        settings = Settings()
        usd_eur_rate = settings.USD_EUR_RATE if usd_eur_rate is None else usd_eur_rate
        budget_eur = settings.BUDGET_EUR_TOTAL if budget_eur is None else budget_eur

    async with SqliteStore(db_path) as store:
        runs = await store.list_runs()
        metrics = await store.list_metrics()

    completion: dict[str, ScenarioCompletion] = {}
    for scenario in sorted({r.scenario or "unlabeled" for r in runs}):
        group = [r for r in runs if (r.scenario or "unlabeled") == scenario]
        # "written_despite_failing_tests" is COMPLETED but not a success.
        done = sum(r.status is ProcessStatus.COMPLETED and r.terminal_reason is None for r in group)
        completion[scenario] = ScenarioCompletion(runs=len(group), completed=done, rate=done / len(group))

    by_step: defaultdict[str, list[float]] = defaultdict(list)
    by_model: defaultdict[str, list[float]] = defaultdict(list)
    waits: list[float] = []
    for m in metrics:
        by_step[m.step_name].append(m.compute_ms)
        if m.model_id:
            by_model[m.model_id].append(m.compute_ms)
        if m.step_name in approval_steps:
            waits.append(m.wait_ms)

    cost_usd = sum(m.cost_usd for m in metrics)
    cost_eur = cost_usd * usd_eur_rate
    n_runs = len(runs)
    return EvaluationReport(
        runs=n_runs,
        completion_by_scenario=completion,
        compute_by_step={k: _stats(v) for k, v in sorted(by_step.items())},
        compute_by_model={k: _stats(v) for k, v in sorted(by_model.items())},
        human_wait=_stats(waits),
        avg_fix_iterations=sum(r.fix_iteration for r in runs) / n_runs if n_runs else 0.0,
        avg_revision_iterations=sum(r.revision_iteration for r in runs) / n_runs if n_runs else 0.0,
        prompt_tokens=sum(m.prompt_tokens for m in metrics),
        completion_tokens=sum(m.completion_tokens for m in metrics),
        cost_usd=cost_usd,
        cost_eur=cost_eur,
        budget_eur=budget_eur,
        budget_used_pct=100 * cost_eur / budget_eur,
    )

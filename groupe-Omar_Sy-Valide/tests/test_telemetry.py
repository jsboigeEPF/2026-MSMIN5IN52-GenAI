from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from src.models.state import ApprovalDecision, ProcessStatus, StepMetrics, VerificationResult
from src.observability import telemetry as t
from src.storage.sqlite_store import SqliteStore
from tests.conftest import T0, StateFactory


@pytest.fixture
def traced() -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def test_step_span_is_child_of_run_span(traced: tuple[Tracer, InMemorySpanExporter]) -> None:
    tracer, exporter = traced
    metrics = StepMetrics(
        step_name="plan", iteration=1, started_at=T0, compute_ms=12.0, wait_ms=0.0, status="completed"
    )
    with t.run_span(tracer, "run-1"), t.step_span(tracer, "run-1", "plan", 1) as span:
        t.record_step_metrics(span, metrics)

    step, run = exporter.get_finished_spans()
    assert (run.name, step.name) == ("process.run", "step.plan")
    assert step.parent is not None and step.parent.span_id == run.context.span_id
    assert run.attributes == {t.RUN_ID: "run-1"}
    assert step.attributes is not None
    assert step.attributes[t.STEP_NAME] == "plan"
    assert step.attributes[t.ITERATION] == 1
    assert step.attributes[t.STEP_COMPUTE_MS] == 12.0
    assert step.attributes[t.STEP_WAIT_MS] == 0.0


def test_record_helpers_set_spec_attributes(traced: tuple[Tracer, InMemorySpanExporter]) -> None:
    tracer, exporter = traced
    with tracer.start_as_current_span("s") as span:
        t.record_verification(
            span,
            VerificationResult(pytest_passed=True, pytest_output="", linter_passed=False, linter_output=""),
        )
        t.record_approval(
            span, ApprovalDecision(gate="write", action="approve", actor="alice", decided_at=T0, iteration=0)
        )
        t.record_llm_call(
            span,
            provider="mock",
            service_id="coder",
            model="m",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.25,
        )
    attrs = exporter.get_finished_spans()[0].attributes
    assert attrs == {
        "verification.pytest_passed": True,
        "verification.linter_passed": False,
        "approval.gate": "write",
        "approval.action": "approve",
        "approval.actor": "alice",
        "llm.provider": "mock",
        "llm.service_id": "coder",
        "llm.model": "m",
        "llm.tokens.prompt": 10,
        "llm.tokens.completion": 5,
        "llm.cost_usd": 0.25,
    }


def test_build_tracer_provider_with_console_exporter() -> None:
    provider = t.build_tracer_provider(console=True)
    try:
        assert provider.get_tracer("x") is not None
    finally:
        provider.shutdown()


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], 0.0), ([7.0], 7.0), ([float(v) for v in range(1, 101)], 95.0), ([1.0, 2.0, 3.0, 4.0], 4.0)],
)
def test_percentile_nearest_rank(values: list[float], expected: float) -> None:
    assert t.percentile(values, 95) == expected


async def test_evaluation_report(store: SqliteStore, db_path: Path, make_state: StateFactory) -> None:
    await store.save_state(
        make_state(run_id="a", scenario="nominal", status=ProcessStatus.COMPLETED, fix_iteration=1), T0
    )
    await store.save_state(
        make_state(run_id="b", scenario="nominal", status=ProcessStatus.REJECTED, revision_iteration=2), T0
    )
    await store.save_state(make_state(run_id="c", status=ProcessStatus.EXPIRED), T0)

    def m(
        step: str, compute: float, model: str | None = None, wait: float = 0.0, cost: float = 0.0
    ) -> StepMetrics:
        return StepMetrics(
            step_name=step,
            iteration=0,
            model_id=model,
            started_at=T0,
            compute_ms=compute,
            wait_ms=wait,
            prompt_tokens=100 if model else 0,
            completion_tokens=10 if model else 0,
            cost_usd=cost,
            status="completed",
        )

    await store.append_metric("a", m("plan", 100.0, "vendor/planner", cost=1.0))
    await store.append_metric("b", m("plan", 300.0, "vendor/planner", cost=2.0))
    await store.append_metric("a", m("code", 200.0, "vendor/coder", cost=3.0))
    await store.append_metric("a", m("write_approval", 1.0, wait=5000.0))

    r = await t.generate_evaluation_report(db_path, usd_eur_rate=0.5)

    assert r.runs == 3
    assert r.completion_by_scenario["nominal"].model_dump() == {"runs": 2, "completed": 1, "rate": 0.5}
    assert r.completion_by_scenario["unlabeled"].rate == 0.0
    assert r.compute_by_step["plan"].model_dump() == {"count": 2, "mean_ms": 200.0, "p95_ms": 300.0}
    assert r.compute_by_model["vendor/coder"].mean_ms == 200.0
    assert "write_approval" in r.compute_by_step and None not in r.compute_by_model
    assert r.human_wait.model_dump() == {"count": 1, "mean_ms": 5000.0, "p95_ms": 5000.0}
    assert r.avg_fix_iterations == pytest.approx(1 / 3)
    assert r.avg_revision_iterations == pytest.approx(2 / 3)
    assert (r.prompt_tokens, r.completion_tokens) == (300, 30)
    assert r.cost_usd == pytest.approx(6.0)
    assert r.cost_eur == pytest.approx(3.0)
    assert r.budget_eur == 10.0
    assert r.budget_used_pct == pytest.approx(30.0)


async def test_evaluation_report_on_empty_db(store: SqliteStore, db_path: Path) -> None:
    r = await t.generate_evaluation_report(db_path, usd_eur_rate=1.0)
    assert r.runs == 0 and r.completion_by_scenario == {} and r.cost_usd == 0.0

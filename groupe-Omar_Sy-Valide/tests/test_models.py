from datetime import timedelta

import pytest
from pydantic import ValidationError

from src.models.state import (
    TERMINAL_STATUSES,
    AgentProcessState,
    ApprovalDecision,
    CompensationRecord,
    ProcessStatus,
    StepMetrics,
    VerificationResult,
)
from tests.conftest import T0, StateFactory


def test_state_defaults(make_state: StateFactory) -> None:
    s = make_state()
    assert s.status is ProcessStatus.INITIALIZED
    assert s.current_step == "init"
    assert s.fix_iteration == s.revision_iteration == 0
    assert s.metrics == [] and s.scenario is None


def test_metrics_list_is_not_shared_between_states(make_state: StateFactory) -> None:
    a, b = make_state(), make_state(run_id="run-2")
    a.metrics.append(StepMetrics(step_name="plan", iteration=0, started_at=T0, status="completed"))
    assert b.metrics == []


def test_full_state_json_round_trip(make_state: StateFactory) -> None:
    s = make_state(
        status=ProcessStatus.AWAITING_APPROVAL,
        scenario="nominal",
        execution_plan=["read", "patch"],
        verification=VerificationResult(
            pytest_passed=True, pytest_output="1 passed", linter_passed=False, linter_output="E501"
        ),
        approval_deadline=T0 + timedelta(hours=1),
        compensation=CompensationRecord(action="discard_worktree", done=True, at=T0),
        metrics=[
            StepMetrics(step_name="plan", iteration=0, started_at=T0, cost_usd=0.01, status="completed")
        ],
    )
    assert AgentProcessState.model_validate_json(s.model_dump_json()) == s


def test_status_serializes_as_plain_string(make_state: StateFactory) -> None:
    assert make_state().model_dump(mode="json")["status"] == "INITIALIZED"


def test_terminal_statuses() -> None:
    assert {
        ProcessStatus.COMPLETED,
        ProcessStatus.REJECTED,
        ProcessStatus.EXPIRED,
        ProcessStatus.FAILED,
    } == TERMINAL_STATUSES


def test_approval_action_is_validated() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(action="maybe", actor="alice", decided_at=T0, iteration=0)  # type: ignore[arg-type]

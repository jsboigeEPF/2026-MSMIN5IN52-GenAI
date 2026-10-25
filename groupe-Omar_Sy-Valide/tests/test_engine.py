"""Engine behaviour with the fake LLM. The nine end-to-end scenarios of SPEC §8 live in test_scenarios.py."""

from datetime import timedelta
from pathlib import Path

import pytest
from semantic_kernel import Kernel

from src.clock import FakeClock
from src.config import Settings
from src.llm.client import LLMClient
from src.llm.fake import FakeLLM
from src.models.state import CodePatch, PlanOutput, ProcessStatus
from src.process.engine import DecisionRefusedError, DecisionRequest, ProcessRunner, StartRequest
from src.storage.sqlite_store import SqliteStore
from tests.conftest import BUGGY_CALC, FIXED_CALC, T0, RunnerFactory, git

PLAN = PlanOutput(steps=["change the operator in add()"])
GOOD_PATCH = CodePatch(new_content=FIXED_CALC, rationale="use +")
BAD_PATCH = CodePatch(new_content=BUGGY_CALC.replace("a - b", "a * b"), rationale="wrong")


def start_request(repo: Path) -> StartRequest:
    return StartRequest(
        run_id="run-1",
        target_repo_path=str(repo),
        base_branch="main",
        target_file="calc.py",
        user_prompt="Fix add() so the tests pass",
        scenario="engine",
    )


def approve(actor: str = "alice") -> DecisionRequest:
    return DecisionRequest(action="approve", actor=actor)


async def test_run_suspends_for_approval_with_everything_the_reviewer_needs(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore
) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}), APPROVAL_TIMEOUT_SEC=600)
    run_id = await runner.start(start_request(git_repo))
    state = await runner.status(run_id)

    assert state.status is ProcessStatus.AWAITING_APPROVAL and state.current_step == "approval"
    assert state.execution_plan == PLAN.steps
    assert state.unified_diff is not None and "+    return a + b" in state.unified_diff
    assert state.verification is not None and state.verification.pytest_passed
    assert state.approval_deadline == T0 + timedelta(seconds=600)
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC  # nothing touches the user's tree before approval

    journal = [(e.step, e.event_type) for e in await store.list_events(run_id)]
    assert journal == [
        ("init", "run_started"),
        ("plan", "step_started"),
        ("plan", "step_completed"),
        ("code", "step_started"),
        ("code", "step_completed"),
        ("verify", "step_started"),
        ("verify", "step_completed"),
        ("approval", "step_started"),
        ("approval", "suspended"),
    ]


async def test_approval_finalizes_merges_and_records_human_wait(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore, clock: FakeClock
) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    clock.advance(42)
    state = await runner.decide(run_id, approve())

    assert state.status is ProcessStatus.COMPLETED and state.terminal_reason is None
    assert (git_repo / "calc.py").read_text() == FIXED_CALC
    assert git(git_repo, "rev-parse", "main") == state.git_commit_hash
    assert state.worktree_path is not None and not Path(state.worktree_path).exists()
    assert git(git_repo, "branch", "--list", "mini-opencode/run-1") == ""

    [decision] = await store.list_approvals(run_id)
    assert (decision.action, decision.actor) == ("approve", "alice")
    metrics = await store.list_metrics(run_id)
    assert [m.step_name for m in metrics] == ["plan", "code", "verify", "approval", "finalize"]
    approval = metrics[3]
    assert approval.wait_ms == 42_000 and approval.status == "approve"
    assert metrics[0].prompt_tokens == 100 and metrics[0].model_id == "mock/planner"


async def test_decisions_are_validated(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    with pytest.raises(LookupError):
        await runner.decide("missing", approve())
    run_id = await runner.start(start_request(git_repo))
    with pytest.raises(DecisionRefusedError, match="comment"):
        await runner.decide(run_id, DecisionRequest(action="revise", actor="alice", comment="  "))
    await runner.decide(run_id, approve())
    with pytest.raises(DecisionRefusedError, match="COMPLETED"):
        await runner.decide(run_id, approve())


async def test_failed_verification_loops_then_flags_approval(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [BAD_PATCH, BAD_PATCH]})
    runner = make_runner(fake, MAX_FIX=1)
    run_id = await runner.start(start_request(git_repo))
    state = await runner.status(run_id)

    assert state.status is ProcessStatus.AWAITING_APPROVAL
    assert state.fix_iteration == 2 and state.loop_iteration == 1
    assert state.verification is not None and not state.verification.pytest_passed
    assert fake.calls == {"planner": 1, "coder": 2, "summarizer": 2}
    assert "1 failed" in fake.requests[2].user  # the summarizer got the raw pytest output
    assert state.verification.error_summary is not None
    first_summary = fake.requests[2].user[: fake.summary_chars]  # the fake summarizer truncates its input
    assert first_summary in fake.requests[3].user  # ...and the fix attempt got that summary

    with pytest.raises(DecisionRefusedError, match="verification"):
        await runner.decide(run_id, approve())
    state = await runner.decide(run_id, DecisionRequest(action="reject", actor="bob"))
    assert (state.status, state.terminal_reason) == (ProcessStatus.REJECTED, "human_rejected")
    assert state.compensation is not None and state.compensation.action == "discard_worktree"
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC


async def test_invalid_llm_output_fails_the_run_and_cleans_up(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    runner = make_runner(FakeLLM({"planner": ["nope", "still nope"]}))
    run_id = await runner.start(start_request(git_repo))
    state = await runner.status(run_id)
    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "invalid_llm_output")
    assert state.error_message is not None and "planner" in state.error_message
    assert state.compensation is not None and state.compensation.done
    assert not (git_repo / ".mini-opencode" / "runs" / "run-1").exists()


async def test_finalize_refuses_content_changed_after_approval(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    state = await runner.status(run_id)
    assert state.worktree_path is not None
    (Path(state.worktree_path) / "calc.py").write_text(FIXED_CALC + "import os  # not what was approved\n")

    state = await runner.decide(run_id, approve())

    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "commit_failed")
    assert state.error_message is not None and "approved" in state.error_message
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC
    assert state.git_commit_hash is None


async def test_resume_and_status_are_noops_on_suspended_or_finished_runs(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]})
    runner = make_runner(fake)
    run_id = await runner.start(start_request(git_repo))
    assert (await runner.resume(run_id)).status is ProcessStatus.AWAITING_APPROVAL
    await runner.decide(run_id, approve())
    assert (await runner.resume(run_id)).status is ProcessStatus.COMPLETED
    assert sum(fake.calls.values()) == 2


async def test_runner_without_llm_or_sandbox_can_still_report_and_sweep(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore, clock: FakeClock
) -> None:
    run_id = await make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]})).start(
        start_request(git_repo)
    )
    settings = Settings(_env_file=None)
    bare = ProcessRunner(store=store, llm=None, sandbox=None, settings=settings, clock=clock)
    assert (await bare.status(run_id)).status is ProcessStatus.AWAITING_APPROVAL
    clock.advance(settings.APPROVAL_TIMEOUT_SEC)
    assert await bare.sweep_expired() == [run_id]


async def test_a_step_without_its_sandbox_fails_cleanly(
    git_repo: Path, store: SqliteStore, clock: FakeClock
) -> None:
    settings = Settings(_env_file=None)
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]})
    runner = ProcessRunner(
        store=store,
        llm=LLMClient(Kernel(), fake, store, settings),
        sandbox=None,
        settings=settings,
        clock=clock,
    )
    state = await runner.status(await runner.start(start_request(git_repo)))
    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "sandbox_unavailable")

"""The end-to-end scenarios of SPEC §8, with the fake LLM and FakeClock (no network, no sleep)."""

from pathlib import Path

import pytest

from src.clock import FakeClock
from src.llm.client import LLMUsage
from src.llm.fake import FakeLLM
from src.models.state import CodePatch, PlanOutput, ProcessStatus
from src.process.engine import DecisionRefusedError, DecisionRequest, StartRequest
from src.storage.sqlite_store import SqliteStore
from tests.conftest import BUGGY_CALC, FIXED_CALC, RunnerFactory, git

PLAN = PlanOutput(steps=["replace '-' with '+' in add()"])
GOOD = CodePatch(new_content=FIXED_CALC, rationale="use +")
BAD = CodePatch(new_content=BUGGY_CALC.replace("a - b", "a * b"), rationale="wrong operator")


def request(repo: Path, run_id: str = "run-1") -> StartRequest:
    return StartRequest(
        run_id=run_id,
        target_repo_path=str(repo),
        base_branch="main",
        target_file="calc.py",
        user_prompt="Fix add() so the tests pass",
        scenario="tests",
    )


def decision(action: str, comment: str | None = None) -> DecisionRequest:
    return DecisionRequest.model_validate({"action": action, "actor": "reviewer", "comment": comment})


def worktree(repo: Path, run_id: str = "run-1") -> Path:
    return repo / ".mini-opencode" / "runs" / run_id


# 1 -----------------------------------------------------------------------------------------------------
async def test_nominal(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD]}))
    run_id = await runner.start(request(git_repo))
    assert (await runner.status(run_id)).status is ProcessStatus.AWAITING_APPROVAL

    state = await runner.decide(run_id, decision("approve"))

    assert state.status is ProcessStatus.COMPLETED
    assert git(git_repo, "log", "-1", "--format=%s", "main").startswith("mini-opencode: Fix add()")
    assert (git_repo / "calc.py").read_text() == FIXED_CALC
    assert not worktree(git_repo).exists()


# 2 -----------------------------------------------------------------------------------------------------
async def test_rejection(git_repo: Path, make_runner: RunnerFactory, store: SqliteStore) -> None:
    base_sha = git(git_repo, "rev-parse", "main")
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD]}))
    run_id = await runner.start(request(git_repo))

    state = await runner.decide(run_id, decision("reject", "not the change I want"))

    assert (state.status, state.terminal_reason) == (ProcessStatus.REJECTED, "human_rejected")
    assert state.compensation is not None
    assert (state.compensation.action, state.compensation.done) == ("discard_worktree", True)
    assert git(git_repo, "rev-parse", "main") == base_sha
    assert not worktree(git_repo).exists()
    [stored] = await store.list_approvals(run_id)
    assert (stored.action, stored.comment) == ("reject", "not the change I want")


# 3 -----------------------------------------------------------------------------------------------------
async def test_timeout(git_repo: Path, make_runner: RunnerFactory, clock: FakeClock) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD]}), APPROVAL_TIMEOUT_SEC=300)
    run_id = await runner.start(request(git_repo))
    clock.advance(299)
    assert await runner.sweep_expired() == []

    clock.advance(2)
    assert await runner.sweep_expired() == [run_id]

    state = await runner.status(run_id)
    assert (state.status, state.terminal_reason) == (ProcessStatus.EXPIRED, "approval_expired")
    assert not worktree(git_repo).exists()
    with pytest.raises(DecisionRefusedError):
        await runner.decide(run_id, decision("approve"))


# 4 -----------------------------------------------------------------------------------------------------
async def test_resume_while_awaiting_approval(
    git_repo: Path, make_runner: RunnerFactory, db_path: Path
) -> None:
    first_llm = FakeLLM({"planner": [PLAN], "coder": [GOOD]})
    run_id = await make_runner(first_llm).start(request(git_repo))

    second_llm = FakeLLM({})  # any LLM call from the new runner would fail loudly
    async with SqliteStore(db_path) as reopened:  # new process: new connection, new runner, same database
        state = await make_runner(second_llm, on_store=reopened).decide(run_id, decision("approve"))

    assert state.status is ProcessStatus.COMPLETED
    assert sum(second_llm.calls.values()) == 0
    assert first_llm.calls == {"planner": 1, "coder": 1}


# 5 -----------------------------------------------------------------------------------------------------
async def test_crash_mid_step(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore, db_path: Path
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD]})  # one coder response only: a second call would fail

    def crash_after_llm(point: str) -> None:
        if point == "code.after_llm":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        await make_runner(fake, fault_hook=crash_after_llm).start(request(git_repo))
    crashed = await store.load_state("run-1")
    assert crashed is not None and crashed.current_step == "code" and crashed.unified_diff is None

    async with SqliteStore(db_path) as reopened:
        state = await make_runner(fake, on_store=reopened).resume("run-1")

    assert state.status is ProcessStatus.AWAITING_APPROVAL
    assert fake.calls == {"planner": 1, "coder": 1}  # the coder response came from the LLM cache
    code_starts = [
        e for e in await store.list_events("run-1") if (e.step, e.event_type) == ("code", "step_started")
    ]
    assert len(code_starts) == 2


# 6 -----------------------------------------------------------------------------------------------------
async def test_revision_loop(git_repo: Path, make_runner: RunnerFactory) -> None:
    fake = FakeLLM({"planner": [PLAN, PLAN], "coder": [GOOD, GOOD]})
    runner = make_runner(fake, MAX_REVISIONS=1)
    run_id = await runner.start(request(git_repo))

    state = await runner.decide(run_id, decision("revise", "also add a docstring example"))
    assert state.status is ProcessStatus.AWAITING_APPROVAL
    assert (state.revision_iteration, state.loop_iteration) == (1, 1)
    second_plan_prompt = [r for r in fake.requests if r.service_id == "planner"][1].user
    assert "also add a docstring example" in second_plan_prompt

    state = await runner.decide(run_id, decision("revise", "one more time"))
    assert (state.status, state.terminal_reason) == (ProcessStatus.REJECTED, "max_iterations")
    assert fake.calls["planner"] == 2


# 7 -----------------------------------------------------------------------------------------------------
async def test_auto_fix_loop(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [BAD, GOOD]}), MAX_FIX=2)
    state = await runner.status(await runner.start(request(git_repo)))
    assert state.status is ProcessStatus.AWAITING_APPROVAL
    assert state.fix_iteration == 1
    assert state.verification is not None and state.verification.pytest_passed

    exhausted = make_runner(FakeLLM({"planner": [PLAN], "coder": [BAD, BAD, BAD]}), MAX_FIX=2)
    run_id = await exhausted.start(request(git_repo, "run-2"))
    state = await exhausted.status(run_id)
    assert state.status is ProcessStatus.AWAITING_APPROVAL and state.fix_iteration == 3
    with pytest.raises(DecisionRefusedError, match="verification did not pass"):
        await exhausted.decide(run_id, decision("approve"))


# 8 -----------------------------------------------------------------------------------------------------
async def test_post_commit_compensation(git_repo: Path, make_runner: RunnerFactory) -> None:
    # A test that only exists in the user's working tree: the worktree passes, the base branch does not.
    (git_repo / "tests" / "test_local_only.py").write_text(
        "from calc import add\n\n\ndef test_add_negative() -> None:\n    assert add(-1, -1) == 0\n"
    )
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD]}))
    run_id = await runner.start(request(git_repo))
    state = await runner.decide(run_id, decision("approve"))

    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "post_commit_check_failed")
    assert state.compensation is not None
    assert (state.compensation.action, state.compensation.done) == ("git_revert", True)
    assert git(git_repo, "log", "-1", "--format=%s", "main").startswith("Revert")
    assert git(git_repo, "rev-parse", "main~1") == state.git_commit_hash
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC
    assert not worktree(git_repo).exists()


# 9 -----------------------------------------------------------------------------------------------------
async def test_budget_cap(git_repo: Path, make_runner: RunnerFactory, store: SqliteStore) -> None:
    expensive = FakeLLM({"planner": [PLAN], "coder": [GOOD]}, usage=LLMUsage(prompt_tokens=10, cost_usd=1.0))
    runner = make_runner(expensive, USD_EUR_RATE=1.0, MAX_COST_EUR_PER_RUN=0.5)
    state = await runner.status(await runner.start(request(git_repo)))

    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "budget_exceeded")
    assert expensive.calls == {"planner": 1}
    assert state.compensation is not None and state.compensation.action == "discard_worktree"
    [plan_metric] = await store.list_metrics(state.run_id)
    assert (plan_metric.status, plan_metric.cost_usd) == ("failed", 1.0)  # the paid call is accounted

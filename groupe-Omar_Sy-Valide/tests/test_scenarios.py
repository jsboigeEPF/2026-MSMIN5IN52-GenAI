"""The end-to-end scenarios of SPEC §8, with the fake LLM and FakeClock (no network, no sleep).

Every run goes through two human gates: the plan, then the write of the proposed file.
"""

from pathlib import Path

import pytest

from src.clock import FakeClock
from src.llm.client import LLMUsage
from src.llm.fake import FakeLLM
from src.models.state import CodePatch, PlanOutput, ProcessStatus
from src.process.engine import DecisionRefusedError, DecisionRequest, StartRequest
from src.storage.sqlite_store import SqliteStore
from tests.conftest import BUGGY_CALC, FIXED_CALC, RunnerFactory, git

PLAN = PlanOutput(target_file="calc.py", steps=["replace '-' with '+' in add()"])
GOOD = CodePatch(new_content=FIXED_CALC, rationale="use +")
BAD = CodePatch(new_content=BUGGY_CALC.replace("a - b", "a * b"), rationale="wrong operator")


def request(repo: Path, run_id: str = "run-1", target_file: str | None = "calc.py") -> StartRequest:
    return StartRequest(
        run_id=run_id,
        target_repo_path=str(repo),
        base_branch="main",
        target_file=target_file,
        user_prompt="Fix add() so the tests pass",
        scenario="tests",
    )


def decision(action: str, comment: str | None = None) -> DecisionRequest:
    return DecisionRequest.model_validate({"action": action, "actor": "reviewer", "comment": comment})


def worktree(repo: Path, run_id: str = "run-1") -> Path:
    return repo / ".mini-opencode" / "runs" / run_id


# 1 -----------------------------------------------------------------------------------------------------
async def test_nominal(git_repo: Path, make_runner: RunnerFactory) -> None:
    base_sha = git(git_repo, "rev-parse", "main")
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD]}))
    run_id = await runner.start(request(git_repo, target_file=None))  # the planner picks calc.py
    assert (await runner.status(run_id)).current_step == "plan_approval"
    assert (await runner.decide(run_id, decision("approve"))).current_step == "write_approval"

    state = await runner.decide(run_id, decision("approve"))

    assert state.status is ProcessStatus.COMPLETED
    assert (git_repo / "calc.py").read_text() == FIXED_CALC
    assert git(git_repo, "rev-parse", "main") == base_sha  # written, not committed
    assert not worktree(git_repo).exists()


# 2 -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("gate", ["plan", "write"])
async def test_rejection(git_repo: Path, make_runner: RunnerFactory, store: SqliteStore, gate: str) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD]})
    runner = make_runner(fake)
    run_id = await runner.start(request(git_repo))
    if gate == "write":
        await runner.decide(run_id, decision("approve"))

    state = await runner.decide(run_id, decision("reject", "not the change I want"))

    assert (state.status, state.terminal_reason) == (ProcessStatus.REJECTED, "human_rejected")
    assert state.compensation is not None
    assert (state.compensation.action, state.compensation.done) == ("discard_worktree", True)
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC
    assert not worktree(git_repo).exists()
    assert fake.calls["coder"] == (1 if gate == "write" else 0)
    stored = (await store.list_approvals(run_id))[-1]
    assert (stored.gate, stored.action, stored.comment) == (gate, "reject", "not the change I want")


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
    first = make_runner(first_llm)
    run_id = await first.start(request(git_repo))
    await first.decide(run_id, decision("approve"))

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

    crashing = make_runner(fake, fault_hook=crash_after_llm)
    run_id = await crashing.start(request(git_repo))
    with pytest.raises(RuntimeError, match="simulated crash"):
        await crashing.decide(run_id, decision("approve"))
    crashed = await store.load_state("run-1")
    assert crashed is not None and crashed.current_step == "code" and crashed.unified_diff is None

    async with SqliteStore(db_path) as reopened:
        state = await make_runner(fake, on_store=reopened).resume("run-1")

    assert state.status is ProcessStatus.AWAITING_APPROVAL and state.current_step == "write_approval"
    assert fake.calls == {"planner": 1, "coder": 1}  # the coder response came from the LLM cache
    code_starts = [
        e for e in await store.list_events("run-1") if (e.step, e.event_type) == ("code", "step_started")
    ]
    assert len(code_starts) == 2


# 6 -----------------------------------------------------------------------------------------------------
async def test_refine_plan_then_refine_diff(git_repo: Path, make_runner: RunnerFactory) -> None:
    fake = FakeLLM({"planner": [PLAN, PLAN], "coder": [GOOD, GOOD]})
    runner = make_runner(fake, MAX_REVISIONS=2)
    run_id = await runner.start(request(git_repo))

    state = await runner.decide(run_id, decision("revise", "also add a docstring example"))
    assert state.current_step == "plan_approval"
    assert (state.revision_iteration, state.loop_iteration) == (1, 1)
    second_plan_prompt = [r for r in fake.requests if r.service_id == "planner"][1].user
    assert "also add a docstring example" in second_plan_prompt

    await runner.decide(run_id, decision("approve"))
    state = await runner.decide(run_id, decision("revise", "keep the type hints"))
    assert state.current_step == "write_approval" and state.revision_iteration == 2
    second_code_prompt = [r for r in fake.requests if r.service_id == "coder"][1].user
    assert "keep the type hints" in second_code_prompt and "Your previous proposal" in second_code_prompt
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC

    state = await runner.decide(run_id, decision("revise", "one more time"))
    assert (state.status, state.terminal_reason) == (ProcessStatus.REJECTED, "max_iterations")


# 7 -----------------------------------------------------------------------------------------------------
async def test_auto_fix_loop(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [BAD, GOOD]}), MAX_FIX=2)
    run_id = await runner.start(request(git_repo))
    await runner.decide(run_id, decision("approve"))
    state = await runner.decide(run_id, decision("approve"))  # BAD fails its tests
    assert state.current_step == "write_approval" and state.fix_iteration == 1
    state = await runner.decide(run_id, decision("approve"))
    assert state.status is ProcessStatus.COMPLETED
    assert state.verification is not None and state.verification.pytest_passed

    exhausted = make_runner(FakeLLM({"planner": [PLAN], "coder": [BAD, BAD, BAD]}), MAX_FIX=2)
    run_id = await exhausted.start(request(git_repo, "run-2"))
    for _ in range(4):
        state = await exhausted.decide(run_id, decision("approve"))
    assert state.current_step == "write_approval" and state.fix_iteration == 3  # back to the human
    state = await exhausted.decide(run_id, decision("reject"))
    assert (state.status, state.terminal_reason) == (ProcessStatus.REJECTED, "human_rejected")


# 8 -----------------------------------------------------------------------------------------------------
async def test_write_conflict(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD]}))
    run_id = await runner.start(request(git_repo))
    await runner.decide(run_id, decision("approve"))
    edited = BUGGY_CALC + "# the user kept working meanwhile\n"
    (git_repo / "calc.py").write_text(edited)

    state = await runner.decide(run_id, decision("approve"))

    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "write_failed")
    assert state.compensation is not None and state.compensation.action == "discard_worktree"
    assert (git_repo / "calc.py").read_text() == edited
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


# 10 ----------------------------------------------------------------------------------------------------
async def test_project_without_tests_and_with_old_lint_debt(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    """The cuisine_rag case: no test suite, and lint rules the original file already broke."""
    git(git_repo, "rm", "-q", "-r", "tests")
    (git_repo / "pyproject.toml").write_text('[tool.ruff.lint]\nextend-select = ["RUF100", "UP"]\n')
    legacy = "from typing import Optional\n\n\ndef add(a: Optional[int], b: int) -> int:  # noqa: E501\n"
    (git_repo / "calc.py").write_text(legacy + "    return a - b\n")
    git(git_repo, "commit", "-qam", "no tests, lint debt")
    patch = CodePatch(new_content=legacy + "    return (a or 0) + b\n", rationale="fix")
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [patch]}))
    run_id = await runner.start(request(git_repo))
    await runner.decide(run_id, decision("approve"))
    state = await runner.decide(run_id, decision("approve"))
    assert (state.status, state.terminal_reason) == (ProcessStatus.COMPLETED, None), state.verification


# 11 ----------------------------------------------------------------------------------------------------
async def test_a_css_change_is_never_linted_as_python(git_repo: Path, make_runner: RunnerFactory) -> None:
    css = PlanOutput(target_file="frontend/app/globals.css", steps=["add a body margin"])
    patch = CodePatch(new_content="body {\n  margin: 0;\n}\n", rationale="reset")
    runner = make_runner(FakeLLM({"planner": [css], "coder": [patch]}))
    run_id = await runner.start(request(git_repo, target_file=None))
    await runner.decide(run_id, decision("approve"))
    state = await runner.decide(run_id, decision("approve"))
    assert (state.status, state.terminal_reason) == (ProcessStatus.COMPLETED, None), state.verification
    assert state.verification is not None and state.verification.lint_label != "ruff"
    assert "nothing to run" in state.verification.pytest_output


async def test_omar_toml_commands_verify_other_languages(git_repo: Path, make_runner: RunnerFactory) -> None:
    (git_repo / "check_css.py").write_text("import sys\n\nsys.exit('red' in open('style.css').read())\n")
    (git_repo / ".omar.toml").write_text('[checks]\ntest = "python3 check_css.py"\n')
    css = PlanOutput(target_file="style.css", steps=["blue text"])
    red = CodePatch(new_content="p { color: red; }\n", rationale="r")
    blue = CodePatch(new_content="p { color: blue; }\n", rationale="b")
    runner = make_runner(FakeLLM({"planner": [css], "coder": [red, blue]}))
    run_id = await runner.start(request(git_repo, target_file=None))
    await runner.decide(run_id, decision("approve"))
    state = await runner.decide(run_id, decision("approve"))  # "red" fails the project's own test
    assert state.current_step == "write_approval" and state.fix_iteration == 1
    state = await runner.decide(run_id, decision("approve"))
    assert state.status is ProcessStatus.COMPLETED
    assert state.verification is not None and state.verification.tests_label == "python3 check_css.py"

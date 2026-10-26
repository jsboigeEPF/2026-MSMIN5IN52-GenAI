"""Engine behaviour with the fake LLM. The nine end-to-end scenarios of SPEC §8 live in test_scenarios.py."""

from datetime import timedelta
from pathlib import Path

import pytest
from semantic_kernel import Kernel

from src.clock import FakeClock
from src.config import Settings
from src.llm.client import ChatRequest, ChatResponse, LLMClient, LLMProviderError
from src.llm.fake import FakeLLM
from src.models.state import CodePatch, PlanOutput, ProcessStatus
from src.process.engine import DecisionRefusedError, DecisionRequest, ProcessRunner, StartRequest
from src.storage.sqlite_store import SqliteStore
from tests.conftest import BUGGY_CALC, FIXED_CALC, T0, RunnerFactory, git

PLAN = PlanOutput(target_file="calc.py", steps=["change the operator in add()"])
GOOD_PATCH = CodePatch(new_content=FIXED_CALC, rationale="use +")
BAD_PATCH = CodePatch(new_content=BUGGY_CALC.replace("a - b", "a * b"), rationale="wrong")


def start_request(repo: Path, target_file: str | None = "calc.py") -> StartRequest:
    return StartRequest(
        run_id="run-1",
        target_repo_path=str(repo),
        base_branch="main",
        target_file=target_file,
        user_prompt="Fix add() so the tests pass",
        scenario="engine",
    )


def approve(actor: str = "alice") -> DecisionRequest:
    return DecisionRequest(action="approve", actor=actor)


async def test_run_suspends_at_the_plan_gate_before_any_code_exists(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]})
    runner = make_runner(fake, APPROVAL_TIMEOUT_SEC=600)
    run_id = await runner.start(start_request(git_repo))
    state = await runner.status(run_id)

    assert state.status is ProcessStatus.AWAITING_APPROVAL and state.current_step == "plan_approval"
    assert state.execution_plan == PLAN.steps and state.original_file_content == BUGGY_CALC
    assert state.generated_code is None and fake.calls == {"planner": 1}
    assert state.approval_deadline == T0 + timedelta(seconds=600)
    journal = [(e.step, e.event_type) for e in await store.list_events(run_id)]
    assert journal == [
        ("init", "run_started"),
        ("plan", "step_started"),
        ("plan", "step_completed"),
        ("plan_approval", "step_started"),
        ("plan_approval", "suspended"),
    ]


async def test_write_gate_shows_the_diff_before_anything_is_written_or_run(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    state = await runner.decide(run_id, approve())

    assert state.status is ProcessStatus.AWAITING_APPROVAL and state.current_step == "write_approval"
    assert state.unified_diff is not None and "+    return a + b" in state.unified_diff
    assert state.verification is None  # no generated code has run yet
    assert state.worktree_path is not None
    assert (Path(state.worktree_path) / "calc.py").read_text() == BUGGY_CALC  # not even in the worktree
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC


async def test_approving_both_gates_writes_the_file_without_committing(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore, clock: FakeClock
) -> None:
    base_sha = git(git_repo, "rev-parse", "main")
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    clock.advance(10)
    await runner.decide(run_id, approve())
    clock.advance(42)
    state = await runner.decide(run_id, approve())

    assert state.status is ProcessStatus.COMPLETED and state.terminal_reason is None
    assert state.verification is not None and state.verification.pytest_passed
    assert (git_repo / "calc.py").read_text() == FIXED_CALC
    assert git(git_repo, "rev-parse", "main") == base_sha  # no commit
    assert git(git_repo, "status", "--porcelain") == "M calc.py"
    assert state.worktree_path is not None and not Path(state.worktree_path).exists()
    assert git(git_repo, "branch", "--list", "mini-opencode/run-1") == ""

    decisions = await store.list_approvals(run_id)
    assert [(d.gate, d.action, d.actor) for d in decisions] == [
        ("plan", "approve", "alice"),
        ("write", "approve", "alice"),
    ]
    metrics = await store.list_metrics(run_id)
    assert [m.step_name for m in metrics] == [
        "plan",
        "plan_approval",
        "code",
        "write_approval",
        "verify",
        "finalize",
    ]
    assert (metrics[1].wait_ms, metrics[3].wait_ms) == (10_000, 42_000)
    assert metrics[0].prompt_tokens == 100 and metrics[0].model_id == "mock/planner"


async def test_planner_chooses_the_target_file_when_none_is_given(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]})
    run_id = await make_runner(fake).start(start_request(git_repo, target_file=None))
    state = await make_runner(fake).status(run_id)

    assert state.target_file == "calc.py" and not state.file_locked
    assert state.original_file_content == BUGGY_CALC
    prompt = fake.requests[0].user
    assert "calc.py" in prompt and "tests/test_calc.py" in prompt and "def add(a: int, b: int)" in prompt


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside.py", ".git/config", "  "])
async def test_an_unsafe_target_file_from_the_planner_fails_the_run(
    git_repo: Path, make_runner: RunnerFactory, bad: str
) -> None:
    plan = PlanOutput(target_file=bad, steps=["x"])
    runner = make_runner(FakeLLM({"planner": [plan]}))
    state = await runner.status(await runner.start(start_request(git_repo, target_file=None)))
    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "invalid_llm_output")
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC


async def test_decisions_are_validated(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    with pytest.raises(LookupError):
        await runner.decide("missing", approve())
    run_id = await runner.start(start_request(git_repo))
    with pytest.raises(DecisionRefusedError, match="comment"):
        await runner.decide(run_id, DecisionRequest(action="revise", actor="alice", comment="  "))
    await runner.decide(run_id, approve())
    await runner.decide(run_id, approve())
    with pytest.raises(DecisionRefusedError, match="COMPLETED"):
        await runner.decide(run_id, approve())


async def test_failed_verification_resubmits_the_fix_to_the_write_gate(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [BAD_PATCH, GOOD_PATCH]})
    runner = make_runner(fake)
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, approve())
    state = await runner.decide(run_id, approve())  # writes BAD_PATCH in the worktree, tests fail

    assert state.status is ProcessStatus.AWAITING_APPROVAL and state.current_step == "write_approval"
    assert state.fix_iteration == 1 and state.loop_iteration == 1
    assert state.verification is not None and not state.verification.pytest_passed
    assert state.generated_code == FIXED_CALC  # the fix waits for a human before it runs
    assert "1 failed" in fake.requests[2].user  # the summarizer got the raw pytest output
    first_summary = fake.requests[2].user[: fake.summary_chars]
    assert first_summary in fake.requests[3].user  # ...and the fix attempt got that summary
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC

    state = await runner.decide(run_id, approve())
    assert state.status is ProcessStatus.COMPLETED
    assert (git_repo / "calc.py").read_text() == FIXED_CALC


async def test_exhausted_fixes_hand_the_failing_code_back_to_the_human(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [BAD_PATCH, BAD_PATCH]}), MAX_FIX=1)
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, approve())
    await runner.decide(run_id, approve())
    state = await runner.decide(run_id, approve())  # second BAD_PATCH fails too: no fix left

    assert state.status is ProcessStatus.AWAITING_APPROVAL and state.current_step == "write_approval"
    assert state.fix_iteration == 2 and state.verification is not None
    assert not state.verification.pytest_passed
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC

    state = await runner.decide(run_id, DecisionRequest(action="write_anyway", actor="alice"))
    assert (state.status, state.terminal_reason) == (ProcessStatus.COMPLETED, "written_despite_failing_tests")
    assert (git_repo / "calc.py").read_text() == BAD_PATCH.new_content


async def test_refining_after_exhaustion_gives_a_fresh_fix_budget(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [BAD_PATCH, GOOD_PATCH]})
    runner = make_runner(fake, MAX_FIX=0)
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, approve())
    state = await runner.decide(run_id, approve())  # BAD_PATCH fails, MAX_FIX=0: back to the human
    assert state.current_step == "write_approval" and state.fix_iteration == 1

    state = await runner.decide(run_id, DecisionRequest(action="revise", actor="a", comment="use +"))
    assert state.current_step == "write_approval" and state.fix_iteration == 0
    coder_prompt = [r for r in fake.requests if r.service_id == "coder"][-1].user
    assert "It failed verification" in coder_prompt and "use +" in coder_prompt  # the error is kept
    with pytest.raises(DecisionRefusedError, match="write_anyway"):  # new code: no shortcut past the tests
        await runner.decide(run_id, DecisionRequest(action="write_anyway", actor="a"))
    state = await runner.decide(run_id, approve())  # new code: tested, not written blindly
    assert (state.status, state.terminal_reason) == (ProcessStatus.COMPLETED, None)
    assert state.verification is not None and state.verification.pytest_passed


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


async def test_finalize_refuses_to_overwrite_a_file_edited_during_the_run(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, approve())
    (git_repo / "calc.py").write_text(BUGGY_CALC + "# edited by the user meanwhile\n")

    state = await runner.decide(run_id, approve())

    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "write_failed")
    assert state.error_message is not None and "nothing was overwritten" in state.error_message
    assert (git_repo / "calc.py").read_text().endswith("# edited by the user meanwhile\n")


async def test_undo_restores_the_file_only_if_unchanged(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    with pytest.raises(DecisionRefusedError, match="nothing to undo"):
        await runner.undo(run_id)
    await runner.decide(run_id, approve())
    await runner.decide(run_id, approve())

    (git_repo / "calc.py").write_text(FIXED_CALC + "# later edit\n")
    with pytest.raises(DecisionRefusedError, match="modified"):
        await runner.undo(run_id)
    (git_repo / "calc.py").write_text(FIXED_CALC)

    state = await runner.undo(run_id)
    assert state.undone and (git_repo / "calc.py").read_text() == BUGGY_CALC
    assert git(git_repo, "status", "--porcelain") == ""
    with pytest.raises(DecisionRefusedError, match="nothing to undo"):
        await runner.undo(run_id)


async def test_on_event_hook_sees_every_journal_entry(
    git_repo: Path, store: SqliteStore, clock: FakeClock
) -> None:
    seen: list[tuple[str, str]] = []
    settings = Settings(_env_file=None)
    runner = ProcessRunner(
        store=store,
        llm=LLMClient(Kernel(), FakeLLM({"planner": [PLAN]}), store, settings),
        sandbox=None,
        settings=settings,
        clock=clock,
        on_event=lambda state, step, event, payload: seen.append((step, event)),
    )
    run_id = await runner.start(start_request(git_repo))
    assert seen == [(e.step, e.event_type) for e in await store.list_events(run_id)]
    assert seen[-1] == ("plan_approval", "suspended")


async def test_resume_and_status_are_noops_on_suspended_or_finished_runs(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]})
    runner = make_runner(fake)
    run_id = await runner.start(start_request(git_repo))
    assert (await runner.resume(run_id)).status is ProcessStatus.AWAITING_APPROVAL
    await runner.decide(run_id, approve())
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
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, approve())
    state = await runner.decide(run_id, approve())
    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "sandbox_unavailable")


class FlakyBackend:
    """Fails like a provider outage on the first call, then behaves like the given fake."""

    provider = "mock"

    def __init__(self, fake: FakeLLM) -> None:
        self.fake, self.failed = fake, False

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if not self.failed:
            self.failed = True
            raise LLMProviderError("planner (vendor/m): HTTP 404: model blocked")
        return await self.fake.complete(request)


async def test_provider_error_leaves_the_run_resumable(
    git_repo: Path, store: SqliteStore, clock: FakeClock, make_runner: RunnerFactory
) -> None:
    settings = Settings(_env_file=None, SANDBOX_MODE="subprocess")
    fake = FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]})
    flaky = ProcessRunner(
        store=store,
        llm=LLMClient(Kernel(), FlakyBackend(fake), store, settings),
        sandbox=None,
        settings=settings,
        clock=clock,
    )
    with pytest.raises(LLMProviderError):
        await flaky.start(start_request(git_repo))
    stuck = await store.load_state("run-1")
    assert stuck is not None and (stuck.status, stuck.current_step) == (ProcessStatus.PLANNING, "plan")

    state = await make_runner(fake).resume("run-1")
    assert state.status is ProcessStatus.AWAITING_APPROVAL


async def test_verification_sees_the_users_uncommitted_changes(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    # A helper that only exists, uncommitted, in the user's tree: the tests must run against it.
    (git_repo / "helpers.py").write_text("OFFSET = 0\n")
    (git_repo / "tests" / "test_calc.py").write_text(
        "from calc import add\nfrom helpers import OFFSET\n\n\ndef test_add() -> None:\n"
        "    assert add(2, 3) == 5 + OFFSET\n"
    )
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, approve())
    state = await runner.decide(run_id, approve())
    assert state.status is ProcessStatus.COMPLETED, state.verification


async def test_finalize_replayed_after_writing_completes_the_run(
    git_repo: Path, make_runner: RunnerFactory, store: SqliteStore
) -> None:
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [GOOD_PATCH]}))
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, approve())
    state = await runner.decide(run_id, approve())
    assert state.status is ProcessStatus.COMPLETED
    # Simulate a crash after the write but before the transition was saved: replay FINALIZE.
    state.status, state.current_step, state.terminal_reason = ProcessStatus.FINALIZING, "finalize", None
    assert state.worktree_path is not None
    Path(state.worktree_path).mkdir(parents=True)
    (Path(state.worktree_path) / "calc.py").write_text(FIXED_CALC)
    await store.save_state(state, T0)

    state = await make_runner(FakeLLM({})).resume(run_id)
    assert state.status is ProcessStatus.COMPLETED
    assert (git_repo / "calc.py").read_text() == FIXED_CALC


async def test_planner_choosing_a_directory_fails_the_run(git_repo: Path, make_runner: RunnerFactory) -> None:
    runner = make_runner(FakeLLM({"planner": [PlanOutput(target_file="tests", steps=["x"])]}))
    state = await runner.status(await runner.start(start_request(git_repo, target_file=None)))
    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "invalid_llm_output")


async def test_undo_restores_an_empty_file_and_deletes_a_created_one(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    (git_repo / "empty.py").write_text("")
    (git_repo / "calc.py").write_text(FIXED_CALC)  # uncommitted: the worktree gets it, tests pass
    for run_id, target, existed in (("run-1", "empty.py", True), ("run-2", "brand_new.py", False)):
        plan = PlanOutput(target_file=target, steps=["x"])
        patch = CodePatch(new_content="X = 1\n", rationale="r")
        runner = make_runner(FakeLLM({"planner": [plan], "coder": [patch]}))
        request = start_request(git_repo, target_file=None).model_copy(update={"run_id": run_id})
        await runner.start(request)
        await runner.decide(run_id, approve())
        assert (await runner.decide(run_id, approve())).status is ProcessStatus.COMPLETED
        await runner.undo(run_id)
        assert (git_repo / target).exists() is existed
    assert (git_repo / "empty.py").read_text() == ""


async def test_stale_feedback_is_not_resent(git_repo: Path, make_runner: RunnerFactory) -> None:
    fake = FakeLLM({"planner": [PLAN, PLAN], "coder": [GOOD_PATCH]})
    runner = make_runner(fake)
    run_id = await runner.start(start_request(git_repo))
    await runner.decide(run_id, DecisionRequest(action="revise", actor="a", comment="use a dict"))
    await runner.decide(run_id, approve())
    coder_prompt = [r for r in fake.requests if r.service_id == "coder"][0].user
    assert "use a dict" not in coder_prompt


async def test_planner_overview_survives_deleted_and_binary_python_files(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    (git_repo / "old.py").write_text("x = 1\n")
    (git_repo / "latin1.py").write_bytes(b"s = '\xe9'\n")
    git(git_repo, "add", "old.py", "latin1.py")
    git(git_repo, "commit", "-q", "-m", "more files")
    (git_repo / "old.py").unlink()  # tracked, deleted in the working tree
    fake = FakeLLM({"planner": [PLAN]})
    state = await make_runner(fake).status(await make_runner(fake).start(start_request(git_repo, None)))
    assert state.current_step == "plan_approval"


async def test_starting_on_another_branch_than_the_checked_out_one_is_refused(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    request = start_request(git_repo).model_copy(update={"base_branch": "release"})
    with pytest.raises(ValueError, match="check out 'release'"):
        await make_runner(FakeLLM({})).start(request)


async def test_generated_code_with_terminal_escapes_is_refused(
    git_repo: Path, make_runner: RunnerFactory
) -> None:
    # ESC[2K erases the line on screen: the reviewer would approve code they never saw.
    hidden = {"new_content": FIXED_CALC + "import os  # \x1b[2K\n", "rationale": "r"}
    runner = make_runner(FakeLLM({"planner": [PLAN], "coder": [hidden, hidden]}))
    run_id = await runner.start(start_request(git_repo))
    state = await runner.decide(run_id, approve())
    assert (state.status, state.terminal_reason) == (ProcessStatus.FAILED, "invalid_llm_output")
    assert state.error_message is not None and "control character" in state.error_message

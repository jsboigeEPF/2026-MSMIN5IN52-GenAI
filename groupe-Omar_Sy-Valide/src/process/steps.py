"""The five steps (SPEC §5.4).

A step reads and updates the state, may make one structured LLM call, calls the deterministic tools itself,
and returns the event it emits. It never chooses the next step: routing is `graph.py`'s job.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from src.clock import Clock
from src.config import Settings
from src.llm.client import LLMClient, LLMError, LLMUsage
from src.models.state import (
    AgentProcessState,
    ApprovalDecision,
    CodePatch,
    ErrorSummary,
    PlanOutput,
    ProcessStatus,
    VerificationResult,
)
from src.process.graph import Event, Step
from src.tools.git_ops import GitError, GitOps
from src.tools.inspector import CodeInspector
from src.tools.patch import PatchEngine
from src.tools.sandbox import SandboxRunner, SandboxUnavailableError
from src.tools.workspace import WorkspaceManager


def _no_fault(point: str) -> None:
    return None


@dataclass
class StepContext:
    llm: LLMClient | None  # None for runners that only report and sweep (status, sweep commands)
    sandbox: SandboxRunner | None
    settings: Settings
    clock: Clock
    fault_hook: Callable[[str], None] = _no_fault  # tests raise here to simulate a crash at a known point
    inspector: CodeInspector = field(default_factory=CodeInspector)
    patch: PatchEngine = field(default_factory=PatchEngine)
    git: GitOps = field(default_factory=GitOps)

    def require_llm(self) -> LLMClient:
        if self.llm is None:
            raise LLMError("this runner was built without an LLM client")
        return self.llm

    def require_sandbox(self) -> SandboxRunner:
        if self.sandbox is None:
            raise SandboxUnavailableError("this runner was built without a sandbox")
        return self.sandbox

    def workspace(self, state: AgentProcessState) -> WorkspaceManager:
        return WorkspaceManager(Path(state.target_repo_path), state.base_branch)


@dataclass(frozen=True)
class StepResult:
    event: Event
    usage: LLMUsage = field(default_factory=LLMUsage)


class StepHandler(Protocol):
    step: Step

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult: ...


def _schema(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema())


def _worktree(state: AgentProcessState) -> Path:
    if state.worktree_path is None:
        raise RuntimeError(f"run {state.run_id} has no worktree: the plan step must run first")
    return Path(state.worktree_path)


def _fenced(code: str) -> str:
    return f"```python\n{code}\n```"


PLANNER_SYSTEM = (
    "You are the planning step of a coding agent. Given a task and one Python file, return a short ordered "
    "plan (3 to 7 steps) to change ONLY that file so the task is done and its tests pass. "
    "Reply with one JSON object matching this schema: {schema}"
)
CODER_SYSTEM = (
    "You are the code-generation step of a coding agent. Follow the plan and return the COMPLETE new content "
    "of the target file (not a diff) plus a one-sentence rationale. The code must pass pytest and ruff. "
    "Reply with one JSON object matching this schema: {schema}"
)
SUMMARIZER_SYSTEM = (
    "Condense failing pytest/ruff output into the facts a developer needs to fix the code: "
    "failing test names, "
    "assertion or error messages, file:line. At most 15 lines, no advice. "
    "Reply with one JSON object matching this schema: {schema}"
)


class PlanStep:
    step = Step.PLAN

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        worktree, branch = ctx.workspace(state).create(state.run_id)
        state.worktree_path, state.run_branch = str(worktree), branch
        target = worktree / state.target_file
        if state.original_file_content is None:
            state.original_file_content = ctx.inspector.read_file(target) if target.exists() else ""
        outline = ctx.inspector.inspect_ast(target) if target.exists() else "(new file)"

        parts = [
            f"Task: {state.user_prompt}",
            f"Target file: {state.target_file}",
            f"Outline:\n{outline}",
            f"Original content:\n{_fenced(state.original_file_content)}",
        ]
        if state.human_feedback:
            parts.append(f"Reviewer feedback on the previous attempt (address it): {state.human_feedback}")
        if state.execution_plan:
            parts.append("Previous plan:\n" + "\n".join(f"- {s}" for s in state.execution_plan))
        result = await ctx.require_llm().structured(
            run_id=state.run_id,
            step=self.step,
            iteration=state.loop_iteration,
            service_id="planner",
            system=PLANNER_SYSTEM.format(schema=_schema(PlanOutput)),
            user="\n\n".join(parts),
            output_type=PlanOutput,
        )
        state.execution_plan = result.output.steps
        return StepResult(Event.PLAN_CREATED, result.usage)


class CodeGenerationStep:
    step = Step.CODE

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        parts = [
            f"Task: {state.user_prompt}",
            f"Target file: {state.target_file}",
            "Plan:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(state.execution_plan or [], 1)),
            f"Original content:\n{_fenced(state.original_file_content or '')}",
        ]
        if state.verification is not None and state.verification.error_summary:
            parts.append(f"Your previous attempt:\n{_fenced(state.generated_code or '')}")
            parts.append(f"It failed verification:\n{state.verification.error_summary}")
        result = await ctx.require_llm().structured(
            run_id=state.run_id,
            step=self.step,
            iteration=state.loop_iteration,
            service_id="coder",
            system=CODER_SYSTEM.format(schema=_schema(CodePatch)),
            user="\n\n".join(parts),
            output_type=CodePatch,
        )
        ctx.fault_hook("code.after_llm")
        state.unified_diff = ctx.patch.apply(_worktree(state), state.target_file, result.output.new_content)
        state.generated_code = result.output.new_content
        return StepResult(Event.CODE_GENERATED, result.usage)


class VerificationStep:
    step = Step.VERIFY

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        worktree = _worktree(state)
        (pytest_ok, pytest_out), (lint_ok, lint_out) = await asyncio.gather(
            ctx.require_sandbox().run_pytest(worktree),
            ctx.require_sandbox().run_linter(worktree, state.target_file),
        )
        usage, summary = LLMUsage(), None
        if not (pytest_ok and lint_ok):
            checks = (("pytest", pytest_ok, pytest_out), ("ruff", lint_ok, lint_out))
            failing = [(name, out) for name, ok, out in checks if not ok]
            result = await ctx.require_llm().structured(
                run_id=state.run_id,
                step=self.step,
                iteration=state.loop_iteration,
                service_id="summarizer",
                system=SUMMARIZER_SYSTEM.format(schema=_schema(ErrorSummary)),
                user="\n\n".join(f"{name} output:\n{out}" for name, out in failing if out),
                output_type=ErrorSummary,
            )
            usage, summary = result.usage, result.output.summary
            state.fix_iteration += 1
        state.verification = VerificationResult(
            pytest_passed=pytest_ok,
            pytest_output=pytest_out,
            linter_passed=lint_ok,
            linter_output=lint_out,
            error_summary=summary,
        )
        passed = pytest_ok and lint_ok
        return StepResult(Event.VERIFICATION_PASSED if passed else Event.VERIFICATION_FAILED, usage)


class DecisionRequest(BaseModel):
    action: Literal["approve", "revise", "reject"]
    actor: str = Field(min_length=1)
    comment: str | None = None


class DecisionRefusedError(RuntimeError):
    pass


class HumanApprovalStep:
    """Suspends the run; a later human decision (or the expiry sweep) produces its event."""

    step = Step.APPROVAL
    _EVENTS = {"approve": Event.APPROVED, "revise": Event.REVISION_REQUESTED, "reject": Event.REJECTED}

    def suspend(self, state: AgentProcessState, ctx: StepContext) -> None:
        now = ctx.clock.now()
        state.status = ProcessStatus.AWAITING_APPROVAL
        state.awaiting_since = now
        state.approval_deadline = now + timedelta(seconds=ctx.settings.APPROVAL_TIMEOUT_SEC)

    def validate(self, state: AgentProcessState, decision: DecisionRequest, now: datetime) -> None:
        if state.status is not ProcessStatus.AWAITING_APPROVAL:
            raise DecisionRefusedError(f"run {state.run_id} is {state.status.value}, not awaiting approval")
        if state.approval_deadline is not None and now >= state.approval_deadline:
            raise DecisionRefusedError(f"run {state.run_id}: the approval deadline has passed")
        verified = state.verification is not None and (
            state.verification.pytest_passed and state.verification.linter_passed
        )
        if decision.action == "approve" and not verified:
            raise DecisionRefusedError("approve is disabled: verification did not pass (revise or reject)")
        if decision.action == "revise" and not (decision.comment and decision.comment.strip()):
            raise DecisionRefusedError("revise needs a comment telling the agent what to change")

    def apply(self, state: AgentProcessState, decision: ApprovalDecision) -> Event:
        if decision.action == "revise":
            state.revision_iteration += 1
            state.human_feedback = decision.comment
        return self._EVENTS[decision.action]


class FinalizationStep:
    step = Step.FINALIZE

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        repo, worktree = Path(state.target_repo_path), _worktree(state)
        if state.run_branch is None:
            raise RuntimeError(f"run {state.run_id} has no run branch")
        message = f"mini-opencode: {state.user_prompt.splitlines()[0][:60]}\n\nRun: {state.run_id}"
        if not self._matches_approved(worktree / state.target_file, state.generated_code):
            state.error_message = (
                f"{state.target_file} changed after it was verified and approved; refusing to commit it"
            )
            return StepResult(Event.COMMIT_FAILED)
        try:
            ctx.git.commit(worktree, state.target_file, message)
            state.git_commit_hash = ctx.git.merge_ff(repo, state.base_branch, state.run_branch)
        except GitError as exc:
            state.error_message = str(exc)
            return StepResult(Event.COMMIT_FAILED)
        passed, output = await ctx.require_sandbox().run_pytest(repo)
        if not passed:
            state.error_message = f"post-commit check failed on {state.base_branch}:\n{output[-2000:]}"
            return StepResult(Event.POST_COMMIT_CHECK_FAILED)
        return StepResult(Event.COMMITTED)

    @staticmethod
    def _matches_approved(path: Path, approved: str | None) -> bool:
        """Byte-for-byte check that the file about to be committed is exactly the approved code."""
        if approved is None or not path.exists():
            return False
        return path.read_bytes() == approved.encode("utf-8")

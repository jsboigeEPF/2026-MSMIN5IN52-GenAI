"""The steps (SPEC §5.4): plan, code, verify, finalize, plus the two human gates.

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
from src.llm.client import LLMClient, LLMError, LLMUsage, StructuredOutputError, TokenStream
from src.models.state import (
    AgentProcessState,
    ApprovalDecision,
    CodePatch,
    ErrorSummary,
    Gate,
    PlanOutput,
    ProcessStatus,
    VerificationResult,
)
from src.process.graph import GATES, Event, Limits, Step, fixes_exhausted
from src.tools.checks import Check, link_node_modules, plan_checks
from src.tools.git_ops import list_files
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
    stream: TokenStream | None = None  # live tokens of the LLM calls (the interactive agent shows them)
    inspector: CodeInspector = field(default_factory=CodeInspector)
    patch: PatchEngine = field(default_factory=PatchEngine)

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


def _target(state: AgentProcessState) -> str:
    if state.target_file is None:
        raise RuntimeError(f"run {state.run_id} has no target file: the plan step must run first")
    return state.target_file


def _read_text(path: Path) -> str:
    """Current content of a user file; "" when it does not exist yet (the planner may create a file)."""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _has_tests(state: AgentProcessState) -> bool:
    """Whether the user's repository has test files: then "no tests collected" is a failure, not a pass."""
    for name in list_files(Path(state.target_repo_path)):
        base = name.rsplit("/", 1)[-1]
        if base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py")):
            return True
    return False


def _holds(path: Path, content: str) -> bool:
    """Byte-for-byte check that the file is exactly `content`."""
    return path.is_file() and path.read_bytes() == content.encode("utf-8")


def _fenced(code: str) -> str:
    return f"```python\n{code}\n```"


PLANNER_SYSTEM = (
    "You are the planning step of a coding agent. Given a task and a repository, choose the ONE file to "
    "change (an existing path from the listing, or a new path if the task needs a new file) and return a "
    "short ordered plan (3 to 7 steps) to change ONLY that file so the task is done and the tests pass. "
    "Reply with one JSON object matching this schema: {schema}"
)
CODER_SYSTEM = (
    "You are the code-generation step of a coding agent. Follow the plan and return the COMPLETE new content "
    "of the target file (not a diff) plus a one-sentence rationale. The code must pass the project's tests "
    "and linter. "
    "Reply with one JSON object matching this schema: {schema}"
)
SUMMARIZER_SYSTEM = (
    "Condense failing test and lint output into the facts a developer needs to fix the code: "
    "failing test names, "
    "assertion or error messages, file:line. At most 15 lines, no advice. "
    "Reply with one JSON object matching this schema: {schema}"
)

MAX_LISTED_FILES = 300
MAX_OUTLINED_FILES = 200  # bounds the parsing work, not only the prompt size
MAX_OUTLINE_CHARS = 12_000


class PlanStep:
    step = Step.PLAN

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        repo = Path(state.target_repo_path)
        worktree, branch = ctx.workspace(state).create(state.run_id)
        state.worktree_path, state.run_branch = str(worktree), branch

        parts = [f"Task: {state.user_prompt}"]
        if state.file_locked:
            target = ctx.patch.resolve(repo, _target(state))
            if state.original_file_content is None:  # read from the user's working tree, not from HEAD
                state.original_file_content, state.target_existed = _read_text(target), target.is_file()
            outline = ctx.inspector.inspect_ast(target) if target.is_file() else "(new file)"
            parts.append(f"Target file (fixed by the user): {state.target_file}\nOutline:\n{outline}")
        else:
            parts.append(self._repository_overview(repo, ctx))
        if state.target_file is not None and state.original_file_content is not None:
            parts.append(f"Current content of {state.target_file}:\n{_fenced(state.original_file_content)}")
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
            stream=ctx.stream,
        )
        if not state.file_locked:
            chosen = Path(result.output.target_file.strip()).as_posix()
            try:
                ctx.patch.resolve(repo, chosen)
            except ValueError as exc:
                message = f"planner chose an invalid target file: {exc}"
                raise StructuredOutputError(message, result.usage) from exc
            if chosen != state.target_file:  # first plan, or the reviewer asked for another file
                state.target_file, state.original_file_content = chosen, None
                state.generated_code = state.unified_diff = None
            if state.original_file_content is None:
                path = ctx.patch.resolve(repo, chosen)
                state.original_file_content, state.target_existed = _read_text(path), path.is_file()
        state.execution_plan = result.output.steps
        state.human_feedback = None  # consumed: the new plan addresses it
        return StepResult(Event.PLAN_CREATED, result.usage)

    @staticmethod
    def _repository_overview(repo: Path, ctx: StepContext) -> str:
        files = list_files(repo)
        listing = "\n".join(files[:MAX_LISTED_FILES])
        if len(files) > MAX_LISTED_FILES:
            listing += f"\n... ({len(files) - MAX_LISTED_FILES} more files)"
        outlines: list[str] = []
        budget = MAX_OUTLINE_CHARS
        for name in [f for f in files if f.endswith(".py")][:MAX_OUTLINED_FILES]:
            if not (repo / name).is_file():
                continue  # tracked but deleted in the working tree
            try:
                outline = f"# {name}\n{ctx.inspector.inspect_ast(repo / name)}"
            except (OSError, UnicodeDecodeError):
                continue
            if len(outline) > budget:
                continue  # skip a large outline, keep the smaller ones that still fit
            outlines.append(outline)
            budget -= len(outline)
        return f"Repository files:\n{listing}\n\nPython outlines:\n" + "\n\n".join(outlines)


class CodeGenerationStep:
    """Generates the full new content and its diff. Nothing is written: the write gate comes first."""

    step = Step.CODE

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        target = _target(state)
        parts = [
            f"Task: {state.user_prompt}",
            f"Target file: {target}",
            "Plan:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(state.execution_plan or [], 1)),
            f"Original content:\n{_fenced(state.original_file_content or '')}",
        ]
        if state.generated_code is not None:
            parts.append(f"Your previous proposal:\n{_fenced(state.generated_code)}")
        if state.verification is not None and state.verification.error_summary:
            parts.append(f"It failed verification:\n{state.verification.error_summary}")
        if state.human_feedback:
            parts.append(f"Reviewer feedback (address it): {state.human_feedback}")
        result = await ctx.require_llm().structured(
            run_id=state.run_id,
            step=self.step,
            iteration=state.loop_iteration,
            service_id="coder",
            system=CODER_SYSTEM.format(schema=_schema(CodePatch)),
            user="\n\n".join(parts),
            output_type=CodePatch,
            stream=ctx.stream,
        )
        ctx.fault_hook("code.after_llm")
        state.generated_code = result.output.new_content
        state.unified_diff = ctx.patch.diff(target, state.original_file_content or "", state.generated_code)
        state.human_feedback = None  # consumed: the new proposal addresses it
        return StepResult(Event.CODE_GENERATED, result.usage)


class VerificationStep:
    """Writes the approved content into the run worktree, then runs the project's tests and linter on it
    (pytest and ruff for Python, the npm scripts for a Node project...: see `tools/checks.py`)."""

    step = Step.VERIFY

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        worktree, target, repo = _worktree(state), _target(state), Path(state.target_repo_path)
        ctx.patch.apply(worktree, target, state.generated_code or "")
        plan = plan_checks(repo, target)
        link_node_modules(repo, worktree, plan)
        (tests_ok, tests_out), (lint_ok, lint_out) = await asyncio.gather(
            self._run_check(plan.tests, state, ctx), self._run_check(plan.lint, state, ctx)
        )
        if plan.note:
            tests_out = f"{tests_out}\n({plan.note})".strip()
        usage, summary = LLMUsage(), None
        tests_label = plan.tests.label if plan.tests else "aucun test"
        lint_label = plan.lint.label if plan.lint else "aucun lint"
        if not (tests_ok and lint_ok):
            checks = ((tests_label, tests_ok, tests_out), (lint_label, lint_ok, lint_out))
            failing = [(name, out) for name, ok, out in checks if not ok]
            result = await ctx.require_llm().structured(
                run_id=state.run_id,
                step=self.step,
                iteration=state.loop_iteration,
                service_id="summarizer",
                system=SUMMARIZER_SYSTEM.format(schema=_schema(ErrorSummary)),
                user="\n\n".join(f"{name} output:\n{out}" for name, out in failing if out),
                output_type=ErrorSummary,
                stream=ctx.stream,
            )
            usage, summary = result.usage, result.output.summary
            state.fix_iteration += 1
        state.verification = VerificationResult(
            pytest_passed=tests_ok,
            pytest_output=tests_out,
            linter_passed=lint_ok,
            linter_output=lint_out,
            error_summary=summary,
            tests_label=tests_label,
            lint_label=lint_label,
        )
        passed = tests_ok and lint_ok
        return StepResult(Event.VERIFICATION_PASSED if passed else Event.VERIFICATION_FAILED, usage)

    @staticmethod
    async def _run_check(check: Check | None, state: AgentProcessState, ctx: StepContext) -> tuple[bool, str]:
        """No known check is a pass: nothing can be run for this kind of file (the note says so)."""
        if check is None:
            return True, ""
        sandbox, worktree = ctx.require_sandbox(), _worktree(state)
        if check.kind == "pytest":
            python = await sandbox.project_python(Path(state.target_repo_path))
            return await sandbox.run_pytest(worktree, allow_no_tests=not _has_tests(state), python=python)
        if check.kind == "ruff":
            return await sandbox.run_linter(worktree, _target(state), baseline=state.original_file_content)
        return await sandbox.run_command(worktree, check)


class DecisionRequest(BaseModel):
    action: Literal["approve", "revise", "reject", "write_anyway"]
    actor: str = Field(min_length=1)
    comment: str | None = None


class DecisionRefusedError(RuntimeError):
    pass


class HumanApprovalStep:
    """Both gates (plan, write): suspends the run; a decision (or the expiry sweep) produces its event."""

    _EVENTS = {
        "approve": Event.APPROVED,
        "revise": Event.REVISION_REQUESTED,
        "reject": Event.REJECTED,
        "write_anyway": Event.WRITE_ANYWAY_REQUESTED,
    }

    @staticmethod
    def gate(state: AgentProcessState) -> Gate | None:
        """The gate the run is waiting on, if any."""
        if state.status is not ProcessStatus.AWAITING_APPROVAL:
            return None
        return next((gate for step, gate in GATES.items() if step.value == state.current_step), None)

    @classmethod
    def write_anyway_allowed(cls, state: AgentProcessState, limits: Limits) -> bool:
        """At the write gate once the automatic fixes ran out: the code shown is the one that failed."""
        return cls.gate(state) == "write" and fixes_exhausted(state, limits)

    def suspend(self, state: AgentProcessState, ctx: StepContext) -> None:
        now = ctx.clock.now()
        state.status = ProcessStatus.AWAITING_APPROVAL
        state.awaiting_since = now
        state.approval_deadline = now + timedelta(seconds=ctx.settings.APPROVAL_TIMEOUT_SEC)

    def validate(
        self, state: AgentProcessState, decision: DecisionRequest, now: datetime, limits: Limits
    ) -> Gate:
        gate = self.gate(state)
        if gate is None:
            raise DecisionRefusedError(f"run {state.run_id} is {state.status.value}, not awaiting approval")
        if state.approval_deadline is not None and now >= state.approval_deadline:
            raise DecisionRefusedError(f"run {state.run_id}: the approval deadline has passed")
        if decision.action == "revise" and not (decision.comment and decision.comment.strip()):
            raise DecisionRefusedError("revise needs a comment telling the agent what to change")
        if decision.action == "write_anyway" and not self.write_anyway_allowed(state, limits):
            raise DecisionRefusedError("write_anyway is only possible once the automatic fixes ran out")
        return gate

    def apply(self, state: AgentProcessState, decision: ApprovalDecision, limits: Limits) -> Event:
        if decision.action == "revise":
            state.revision_iteration += 1
            state.human_feedback = decision.comment
            if not self.write_anyway_allowed(state, limits):
                # The failure belongs to older code than the one shown: a fresh attempt. After the fixes
                # ran out, the shown code is the one that failed, and the coder needs that error.
                state.verification = None
            state.fix_iteration = 0  # a fresh budget of automatic fixes
        return self._EVENTS[decision.action]


class FinalizationStep:
    """Writes the approved, verified content into the user's working tree. No commit."""

    step = Step.FINALIZE

    async def run(self, state: AgentProcessState, ctx: StepContext) -> StepResult:
        target = _target(state)
        approved = state.generated_code
        if approved is None or not _holds(_worktree(state) / target, approved):
            state.error_message = f"{target} changed after it was approved and verified; refusing to write it"
            return StepResult(Event.WRITE_FAILED)
        try:
            path = ctx.patch.resolve(Path(state.target_repo_path), target)
            if _holds(path, approved):  # replayed after a crash that followed the write
                return StepResult(Event.WRITTEN)
            if _read_text(path) != (state.original_file_content or ""):
                state.error_message = (
                    f"{target} was modified in your working tree during the run; nothing was overwritten"
                )
                return StepResult(Event.WRITE_FAILED)
            ctx.patch.apply(Path(state.target_repo_path), target, approved)
        except (OSError, ValueError) as exc:
            state.error_message = f"could not write {target}: {exc}"
            return StepResult(Event.WRITE_FAILED)
        if not _holds(path, approved):
            state.error_message = f"{target} does not hold the approved content after writing"
            return StepResult(Event.WRITE_FAILED)
        return StepResult(Event.WRITTEN)


def undo_write(state: AgentProcessState, patch: PatchEngine) -> str:
    """Restore the file a completed run wrote, if it still holds exactly what the run wrote."""
    if state.status is not ProcessStatus.COMPLETED or state.undone:
        raise DecisionRefusedError(f"run {state.run_id} has nothing to undo ({state.status.value})")
    target = _target(state)
    path = patch.resolve(Path(state.target_repo_path), target)
    if not _holds(path, state.generated_code or ""):
        raise DecisionRefusedError(f"{target} was modified after the run wrote it; undo refused")
    if state.target_existed:
        patch.apply(Path(state.target_repo_path), target, state.original_file_content or "")
    else:
        path.unlink()  # the run created the file
    return target

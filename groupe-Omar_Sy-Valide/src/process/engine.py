"""In-house process engine (SPEC §5.2 option B, see docs/phase0_sk_spike.md).

An async loop over `graph.TRANSITIONS`: run the current step, persist the full state, journal
`step_started` / `step_completed`, follow the transition. The persisted `current_step` always names the
next step to run, so `resume` after a crash re-runs exactly the step that did not complete; the LLM cache
(keyed by run, step, loop iteration and prompt hash) makes that replay free.

Known failures (budget, invalid LLM output, unavailable sandbox) end the run as FAILED with compensation.
Any other exception is a crash: it propagates and leaves the run resumable.

The run suspends on each human gate (`graph.GATES`): `start`, `decide` and `resume` return once the run
waits for a human or has ended. `on_event` lets an interactive front end follow every journal entry live.
"""

import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Tracer
from pydantic import BaseModel, Field

from src.clock import Clock
from src.config import Settings
from src.llm.client import (
    BudgetExceededError,
    LLMClient,
    LLMError,
    LLMUsage,
    StructuredOutputError,
    TokenStream,
)
from src.models.state import (
    TERMINAL_STATUSES,
    AgentProcessState,
    ApprovalDecision,
    ProcessStatus,
    StepMetrics,
)
from src.observability.telemetry import (
    record_approval,
    record_step_metrics,
    record_verification,
    run_span,
    step_span,
)
from src.process.compensation import CompensationHandler
from src.process.graph import (
    GATES,
    STEP_STATUS,
    Event,
    Step,
    Terminal,
    Transition,
    failure,
    next_transition,
)
from src.process.steps import (
    CodeGenerationStep,
    DecisionRefusedError,
    DecisionRequest,
    FinalizationStep,
    HumanApprovalStep,
    PlanStep,
    StepContext,
    StepHandler,
    VerificationStep,
    undo_write,
)
from src.storage.sqlite_store import SqliteStore
from src.tools.git_ops import run_git
from src.tools.sandbox import SandboxRunner, SandboxUnavailableError
from src.tools.workspace import WorkspaceManager

__all__ = ["DecisionRefusedError", "DecisionRequest", "EventHook", "ProcessRunner", "StartRequest"]

END = "end"

# (state after the event, step, event_type, payload): called after each journal entry is persisted.
EventHook = Callable[[AgentProcessState, str, str, dict[str, Any]], None]


class StartRequest(BaseModel):
    target_repo_path: str
    base_branch: str | None = None  # None: the branch checked out in the repository
    target_file: str | None = None  # None: the planner chooses it
    user_prompt: str = Field(min_length=1)
    scenario: str | None = None
    run_id: str | None = None


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, BudgetExceededError):
        return "budget_exceeded"
    if isinstance(exc, StructuredOutputError):
        return "invalid_llm_output"
    if isinstance(exc, SandboxUnavailableError):
        return "sandbox_unavailable"
    return "llm_error"


class ProcessRunner:
    def __init__(
        self,
        *,
        store: SqliteStore,
        llm: LLMClient | None,
        sandbox: SandboxRunner | None,
        settings: Settings,
        clock: Clock,
        tracer: Tracer | None = None,
        fault_hook: Callable[[str], None] | None = None,
        on_event: EventHook | None = None,
        stream: TokenStream | None = None,
    ) -> None:
        self.store = store
        self.on_event = on_event
        self.clock = clock
        self.tracer = tracer or trace.get_tracer(__name__)
        self.limits = settings.limits()
        self.ctx = StepContext(llm=llm, sandbox=sandbox, settings=settings, clock=clock)
        if fault_hook is not None:
            self.ctx.fault_hook = fault_hook
        self.ctx.stream = stream
        self.handlers: dict[Step, StepHandler] = {
            Step.PLAN: PlanStep(),
            Step.CODE: CodeGenerationStep(),
            Step.VERIFY: VerificationStep(),
            Step.FINALIZE: FinalizationStep(),
        }
        self.approval = HumanApprovalStep()
        self.compensation = CompensationHandler(clock)

    # --- public interface (SPEC §5.2) --------------------------------------------------------------------

    async def start(self, request: StartRequest) -> str:
        run_id = request.run_id or uuid.uuid4().hex[:12]
        repo = Path(request.target_repo_path).resolve()
        base = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if request.base_branch is not None and request.base_branch != base:
            # The file is tested on, and written into, the checked-out tree: no other branch makes sense.
            raise ValueError(f"check out '{request.base_branch}' in {repo} first (found '{base}')")
        WorkspaceManager(repo, base).worktree_path(run_id)  # rejects unsafe run ids early
        if await self.store.load_state(run_id) is not None:
            raise ValueError(f"run {run_id} already exists")
        state = AgentProcessState(
            run_id=run_id,
            scenario=request.scenario,
            current_step=Step.PLAN,
            target_repo_path=str(repo),
            base_branch=base,
            target_file=request.target_file,
            file_locked=request.target_file is not None,
            user_prompt=request.user_prompt,
        )
        await self._save(state)
        await self._event(
            state, "init", "run_started", state.loop_iteration, {"target_file": state.target_file}
        )
        with run_span(self.tracer, run_id):
            await self._drive(state)
        return run_id

    async def status(self, run_id: str) -> AgentProcessState:
        await self.sweep_expired()
        return await self._load(run_id)

    async def resume(self, run_id: str) -> AgentProcessState:
        await self.sweep_expired()
        state = await self._load(run_id)
        if state.status in TERMINAL_STATUSES or state.status is ProcessStatus.AWAITING_APPROVAL:
            return state
        with run_span(self.tracer, run_id):
            await self._drive(state)
        return state

    async def decide(self, run_id: str, request: DecisionRequest) -> AgentProcessState:
        await self.sweep_expired()
        state = await self._load(run_id)
        now = self.clock.now()
        gate = self.approval.validate(state, request, now, self.limits)
        step = Step(state.current_step)
        decision = ApprovalDecision(
            gate=gate,
            action=request.action,
            actor=request.actor,
            comment=request.comment,
            decided_at=now,
            iteration=state.loop_iteration,
        )
        await self.store.append_approval(run_id, decision)
        with run_span(self.tracer, run_id):
            event = self.approval.apply(state, decision, self.limits)
            await self._close_approval(state, step, decision, now)
            if await self._follow(state, step, event, state.loop_iteration):
                await self._drive(state)
        return state

    async def undo(self, run_id: str) -> AgentProcessState:
        """Restore the file a completed run wrote, if nobody changed it since."""
        state = await self._load(run_id)
        target = undo_write(state, self.ctx.patch)
        state.undone = True
        await self._save(state)
        await self._event(state, END, "undone", state.loop_iteration, {"target_file": target})
        return state

    async def sweep_expired(self) -> list[str]:
        """Move every run whose approval deadline has passed to EXPIRED (with compensation)."""
        now = self.clock.now()
        expired: list[str] = []
        for state in await self.store.list_runs(status=ProcessStatus.AWAITING_APPROVAL):
            gate = next((s for s in GATES if s.value == state.current_step), None)
            if gate is None or state.approval_deadline is None or now < state.approval_deadline:
                continue  # not overdue, or a run from an older graph version
            step = gate
            with run_span(self.tracer, state.run_id):
                await self._close_approval(state, step, None, now)
                await self._follow(state, step, Event.EXPIRED, state.loop_iteration)
            expired.append(state.run_id)
        return expired

    # --- loop ----------------------------------------------------------------------------------------------

    async def _drive(self, state: AgentProcessState) -> None:
        while True:
            step = Step(state.current_step)
            if step in GATES:
                await self._suspend(state, step)
                return
            if not await self._execute(state, step):
                return

    async def _execute(self, state: AgentProcessState, step: Step) -> bool:
        """Run one step and follow its transition. Returns False once the run has ended."""
        iteration = state.loop_iteration
        started, t0 = self.clock.now(), time.perf_counter()
        state.status = STEP_STATUS[step]
        await self._save(state)  # status shows the running step, also if it crashes
        await self._event(state, step, "step_started", iteration)
        with step_span(self.tracer, state.run_id, step, iteration) as span:
            try:
                result = await self.handlers[step].run(state, self.ctx)
            except (LLMError, SandboxUnavailableError) as exc:
                usage = exc.usage if isinstance(exc, LLMError) else LLMUsage()
                metrics = self._metrics(step, iteration, started, t0, usage, "failed")
                record_step_metrics(span, metrics)
                await self._record(state, metrics)
                reason = _failure_reason(exc)
                state.error_message = f"{type(exc).__name__}: {exc}"
                await self._event(state, step, "step_failed", iteration, {"reason": reason})
                await self._finish(state, failure(reason))
                return False
            metrics = self._metrics(step, iteration, started, t0, result.usage, result.event)
            record_step_metrics(span, metrics)
            if step is Step.VERIFY and state.verification is not None:
                record_verification(span, state.verification)
            await self._record(state, metrics)
        return await self._follow(state, step, result.event, iteration)

    async def _follow(self, state: AgentProcessState, step: Step, event: Event, iteration: int) -> bool:
        """Apply the transition for `event`. Returns True if the run continues to another step."""
        transition: Transition = next_transition(step, event, state, self.limits)
        target = transition.target
        if isinstance(target, Terminal):
            await self._event(state, step, "step_completed", iteration, {"event": event, "next": END})
            await self._finish(state, target)
            return False
        if transition.loop:
            state.loop_iteration += 1
        state.current_step = target
        if target not in GATES:  # AWAITING_APPROVAL is only set once the deadline is persisted
            state.status = STEP_STATUS[target]
        await self._save(state)
        await self._event(state, step, "step_completed", iteration, {"event": event, "next": target})
        return True

    async def _suspend(self, state: AgentProcessState, step: Step) -> None:
        await self._event(state, step, "step_started", state.loop_iteration)
        self.approval.suspend(state, self.ctx)
        await self._save(state)
        deadline = state.approval_deadline.isoformat() if state.approval_deadline else None
        payload = {"gate": GATES[step], "deadline": deadline}
        await self._event(state, step, "suspended", state.loop_iteration, payload)

    async def _close_approval(
        self, state: AgentProcessState, step: Step, decision: ApprovalDecision | None, now: datetime
    ) -> None:
        since = state.awaiting_since or now
        metrics = StepMetrics(
            step_name=step,
            iteration=state.loop_iteration,
            started_at=since,
            ended_at=now,
            wait_ms=(now - since).total_seconds() * 1000,
            status=decision.action if decision else "expired",
        )
        with step_span(self.tracer, state.run_id, step, state.loop_iteration) as span:
            record_step_metrics(span, metrics)
            if decision is not None:
                record_approval(span, decision)
        await self._record(state, metrics)

    async def _finish(self, state: AgentProcessState, terminal: Terminal) -> None:
        if terminal.compensation is not None:
            state.compensation = self.compensation.run(state, terminal.compensation)
        else:  # COMPLETED: the work is merged, the worktree is no longer needed
            WorkspaceManager(Path(state.target_repo_path), state.base_branch).discard(state.run_id)
        state.status = terminal.status
        state.terminal_reason = terminal.reason
        state.current_step = END
        await self._save(state)
        payload = {
            "status": terminal.status,
            "reason": terminal.reason,
            "compensation": terminal.compensation,
        }
        await self._event(state, END, "run_finished", state.loop_iteration, payload)

    # --- persistence helpers -------------------------------------------------------------------------------

    def _metrics(
        self, step: Step, iteration: int, started: datetime, t0: float, usage: LLMUsage, status: str
    ) -> StepMetrics:
        return StepMetrics(
            step_name=step,
            iteration=iteration,
            model_id=usage.model_id,
            started_at=started,
            ended_at=self.clock.now(),
            compute_ms=(time.perf_counter() - t0) * 1000,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=usage.cost_usd,
            status=status,
        )

    async def _record(self, state: AgentProcessState, metrics: StepMetrics) -> None:
        state.metrics.append(metrics)
        await self.store.append_metric(state.run_id, metrics)

    async def _load(self, run_id: str) -> AgentProcessState:
        state = await self.store.load_state(run_id)
        if state is None:
            raise LookupError(f"unknown run {run_id}")
        return state

    async def _save(self, state: AgentProcessState) -> None:
        await self.store.save_state(state, self.clock.now())

    async def _event(
        self,
        state: AgentProcessState,
        step: str,
        event_type: str,
        iteration: int,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self.store.append_event(state.run_id, self.clock.now(), step, iteration, event_type, payload)
        if self.on_event is not None:
            self.on_event(state, step, event_type, payload or {})

"""The process graph as data (SPEC §5.1): the single source of truth for routing.

The runner, the tests and the dashboard all read `TRANSITIONS`. Routing is plain code over the persisted
state: no LLM ever decides a transition.

Two human gates: the plan (before any code is generated) and the write (before the proposed file is written
and tested), so no code runs before a human has read it. The approved file is written to the user's working
tree without a commit. When the automatic fixes are used up, the write gate comes back with the failing code:
the human refines it (fresh fix budget), rejects it, or has it written anyway.

Counters are incremented before routing (a failed verification bumps `fix_iteration`, a "revise" decision
at either gate bumps `revision_iteration`), so the spec's `fix_iteration < MAX_FIX` (counted before the
increment) is written here as `fix_iteration <= max_fix`: MAX_FIX is the number of automatic fix loops
allowed.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from src.models.state import AgentProcessState, Gate, ProcessStatus


class Step(StrEnum):
    PLAN = "plan"
    PLAN_APPROVAL = "plan_approval"
    CODE = "code"
    WRITE_APPROVAL = "write_approval"
    VERIFY = "verify"
    FINALIZE = "finalize"


class Event(StrEnum):
    PLAN_CREATED = "PlanCreated"
    CODE_GENERATED = "CodeGenerated"
    VERIFICATION_PASSED = "VerificationPassed"
    VERIFICATION_FAILED = "VerificationFailed"
    APPROVED = "Approved"
    REVISION_REQUESTED = "RevisionRequested"
    REJECTED = "Rejected"
    WRITE_ANYWAY_REQUESTED = "WriteAnywayRequested"
    EXPIRED = "Expired"
    WRITTEN = "Written"
    WRITE_FAILED = "WriteFailed"


# Human gates: the run suspends on these steps until a decision (or the expiry sweep) produces an event.
GATES: dict[Step, Gate] = {Step.PLAN_APPROVAL: "plan", Step.WRITE_APPROVAL: "write"}


@dataclass(frozen=True)
class Limits:
    max_fix: int
    max_revisions: int


@dataclass(frozen=True)
class Terminal:
    """End of the run. `compensation` says what the CompensationHandler must undo (None: nothing)."""

    status: ProcessStatus
    reason: str | None
    compensation: Literal["discard_worktree"] | None


Guard = Callable[[AgentProcessState, Limits], bool]


@dataclass(frozen=True)
class Transition:
    source: Step
    event: Event
    target: Step | Terminal
    label: str
    guard: Guard | None = None
    loop: bool = False  # feedback edge: starts a new loop iteration

    def allows(self, state: AgentProcessState, limits: Limits) -> bool:
        return self.guard is None or self.guard(state, limits)


def _fix_left(s: AgentProcessState, lim: Limits) -> bool:
    return s.fix_iteration <= lim.max_fix


def fixes_exhausted(s: AgentProcessState, lim: Limits) -> bool:
    """The automatic fixes are used up: the code at the write gate is the last one tested, and it failed.

    A human "refine" resets `fix_iteration`, so new code never reaches the write gate in this state."""
    return not _fix_left(s, lim)


def _verified(s: AgentProcessState, lim: Limits) -> bool:
    return s.verification is not None and s.verification.pytest_passed and s.verification.linter_passed


def _not_verified(s: AgentProcessState, lim: Limits) -> bool:
    return not _verified(s, lim)


def _revision_left(s: AgentProcessState, lim: Limits) -> bool:
    return s.revision_iteration <= lim.max_revisions


def _revision_exhausted(s: AgentProcessState, lim: Limits) -> bool:
    return not _revision_left(s, lim)


def _discard(status: ProcessStatus, reason: str) -> Terminal:
    return Terminal(status, reason, "discard_worktree")


COMPLETED = Terminal(ProcessStatus.COMPLETED, None, None)


def _gate(step: Step, approved: tuple[Transition, ...], revised: Step) -> tuple[Transition, ...]:
    """The exits of a human gate. Both gates share the revision counter."""
    return (
        *approved,
        Transition(
            step,
            Event.REVISION_REQUESTED,
            revised,
            "refine (revision_iteration < MAX_REVISIONS)",
            _revision_left,
            loop=True,
        ),
        Transition(
            step,
            Event.REVISION_REQUESTED,
            _discard(ProcessStatus.REJECTED, "max_iterations"),
            "revisions exhausted",
            _revision_exhausted,
        ),
        Transition(step, Event.REJECTED, _discard(ProcessStatus.REJECTED, "human_rejected"), "rejected"),
        Transition(step, Event.EXPIRED, _discard(ProcessStatus.EXPIRED, "approval_expired"), "expired"),
    )


TRANSITIONS: tuple[Transition, ...] = (
    Transition(Step.PLAN, Event.PLAN_CREATED, Step.PLAN_APPROVAL, "plan ready"),
    *_gate(
        Step.PLAN_APPROVAL,
        approved=(Transition(Step.PLAN_APPROVAL, Event.APPROVED, Step.CODE, "approved"),),
        revised=Step.PLAN,
    ),
    Transition(Step.CODE, Event.CODE_GENERATED, Step.WRITE_APPROVAL, "diff ready, nothing written"),
    *_gate(
        Step.WRITE_APPROVAL,
        approved=(
            Transition(
                Step.WRITE_APPROVAL, Event.APPROVED, Step.VERIFY, "approved: write in the worktree and test"
            ),
            # Its own decision, accepted only once the fixes ran out (HumanApprovalStep.validate):
            # "approve" never means "skip the tests".
            Transition(
                Step.WRITE_APPROVAL, Event.WRITE_ANYWAY_REQUESTED, Step.FINALIZE, "write anyway (tests fail)"
            ),
        ),
        revised=Step.CODE,
    ),
    Transition(Step.VERIFY, Event.VERIFICATION_PASSED, Step.FINALIZE, "tests and lint pass"),
    Transition(
        Step.VERIFY,
        Event.VERIFICATION_FAILED,
        Step.CODE,
        "auto-fix (fix_iteration < MAX_FIX)",
        _fix_left,
        loop=True,
    ),
    Transition(
        Step.VERIFY,
        Event.VERIFICATION_FAILED,
        Step.WRITE_APPROVAL,
        "fixes exhausted: the human decides",
        fixes_exhausted,
    ),
    Transition(Step.FINALIZE, Event.WRITTEN, COMPLETED, "file written, not committed", _verified),
    Transition(
        Step.FINALIZE,
        Event.WRITTEN,
        Terminal(ProcessStatus.COMPLETED, "written_despite_failing_tests", None),
        "written although tests fail",
        _not_verified,
    ),
    Transition(
        Step.FINALIZE, Event.WRITE_FAILED, _discard(ProcessStatus.FAILED, "write_failed"), "write refused"
    ),
)

# Status shown while a step runs (AWAITING_APPROVAL is set when a gate suspends).
STEP_STATUS = {
    Step.PLAN: ProcessStatus.PLANNING,
    Step.PLAN_APPROVAL: ProcessStatus.AWAITING_APPROVAL,
    Step.CODE: ProcessStatus.CODING,
    Step.WRITE_APPROVAL: ProcessStatus.AWAITING_APPROVAL,
    Step.VERIFY: ProcessStatus.VERIFYING,
    Step.FINALIZE: ProcessStatus.FINALIZING,
}


def next_transition(step: Step, event: Event, state: AgentProcessState, limits: Limits) -> Transition:
    for transition in TRANSITIONS:
        if transition.source == step and transition.event == event and transition.allows(state, limits):
            return transition
    raise LookupError(f"no transition from {step} on {event}")


def failure(reason: str) -> Terminal:
    """Engine-level failure outside the table (budget, invalid LLM output, sandbox).

    Always happens before any merge, so the worktree is discarded."""
    return _discard(ProcessStatus.FAILED, reason)

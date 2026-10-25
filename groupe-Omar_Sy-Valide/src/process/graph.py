"""The process graph as data (SPEC §5.1): the single source of truth for routing.

The runner, the tests and the dashboard all read `TRANSITIONS`. Routing is plain code over the persisted
state: no LLM ever decides a transition.

Counters are incremented before routing (a failed verification bumps `fix_iteration`, a "revise" decision
bumps `revision_iteration`), so the spec's `fix_iteration < MAX_FIX` (counted before the increment) is
written here as `fix_iteration <= max_fix`: MAX_FIX is the number of automatic fix loops allowed.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from src.models.state import AgentProcessState, ProcessStatus


class Step(StrEnum):
    PLAN = "plan"
    CODE = "code"
    VERIFY = "verify"
    APPROVAL = "approval"
    FINALIZE = "finalize"


class Event(StrEnum):
    PLAN_CREATED = "PlanCreated"
    CODE_GENERATED = "CodeGenerated"
    VERIFICATION_PASSED = "VerificationPassed"
    VERIFICATION_FAILED = "VerificationFailed"
    APPROVED = "Approved"
    REVISION_REQUESTED = "RevisionRequested"
    REJECTED = "Rejected"
    EXPIRED = "Expired"
    COMMITTED = "Committed"
    COMMIT_FAILED = "CommitFailed"
    POST_COMMIT_CHECK_FAILED = "PostCommitCheckFailed"


@dataclass(frozen=True)
class Limits:
    max_fix: int
    max_revisions: int


@dataclass(frozen=True)
class Terminal:
    """End of the run. `compensation` says what the CompensationHandler must undo (None: nothing)."""

    status: ProcessStatus
    reason: str | None
    compensation: Literal["discard_worktree", "git_revert"] | None


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


def _fix_exhausted(s: AgentProcessState, lim: Limits) -> bool:
    return not _fix_left(s, lim)


def _revision_left(s: AgentProcessState, lim: Limits) -> bool:
    return s.revision_iteration <= lim.max_revisions


def _revision_exhausted(s: AgentProcessState, lim: Limits) -> bool:
    return not _revision_left(s, lim)


def _discard(status: ProcessStatus, reason: str) -> Terminal:
    return Terminal(status, reason, "discard_worktree")


COMPLETED = Terminal(ProcessStatus.COMPLETED, None, None)

TRANSITIONS: tuple[Transition, ...] = (
    Transition(Step.PLAN, Event.PLAN_CREATED, Step.CODE, "plan ready"),
    Transition(Step.CODE, Event.CODE_GENERATED, Step.VERIFY, "patch applied"),
    Transition(Step.VERIFY, Event.VERIFICATION_PASSED, Step.APPROVAL, "tests and lint pass"),
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
        Step.APPROVAL,
        "fixes exhausted, approve disabled",
        _fix_exhausted,
    ),
    Transition(Step.APPROVAL, Event.APPROVED, Step.FINALIZE, "approved"),
    Transition(
        Step.APPROVAL,
        Event.REVISION_REQUESTED,
        Step.PLAN,
        "revise (revision_iteration < MAX_REVISIONS)",
        _revision_left,
        loop=True,
    ),
    Transition(
        Step.APPROVAL,
        Event.REVISION_REQUESTED,
        _discard(ProcessStatus.REJECTED, "max_iterations"),
        "revisions exhausted",
        _revision_exhausted,
    ),
    Transition(Step.APPROVAL, Event.REJECTED, _discard(ProcessStatus.REJECTED, "human_rejected"), "rejected"),
    Transition(Step.APPROVAL, Event.EXPIRED, _discard(ProcessStatus.EXPIRED, "approval_expired"), "expired"),
    Transition(Step.FINALIZE, Event.COMMITTED, COMPLETED, "merged, post-commit check passed"),
    Transition(
        Step.FINALIZE, Event.COMMIT_FAILED, _discard(ProcessStatus.FAILED, "commit_failed"), "commit failed"
    ),
    Transition(
        Step.FINALIZE,
        Event.POST_COMMIT_CHECK_FAILED,
        Terminal(ProcessStatus.FAILED, "post_commit_check_failed", "git_revert"),
        "post-commit check failed",
    ),
)

# Status shown while a step runs (AWAITING_APPROVAL is set when the approval step suspends).
STEP_STATUS = {
    Step.PLAN: ProcessStatus.PLANNING,
    Step.CODE: ProcessStatus.CODING,
    Step.VERIFY: ProcessStatus.VERIFYING,
    Step.APPROVAL: ProcessStatus.AWAITING_APPROVAL,
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

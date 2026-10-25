from itertools import product

import pytest

from src.models.state import ProcessStatus
from src.process.graph import TRANSITIONS, Event, Limits, Step, Terminal, next_transition
from tests.conftest import StateFactory

LIMITS = Limits(max_fix=2, max_revisions=2)


REJECTED = Terminal(ProcessStatus.REJECTED, "human_rejected", "discard_worktree")
EXHAUSTED = Terminal(ProcessStatus.REJECTED, "max_iterations", "discard_worktree")
EXPIRED = Terminal(ProcessStatus.EXPIRED, "approval_expired", "discard_worktree")


@pytest.mark.parametrize(
    ("step", "event", "fix", "rev", "expected"),
    [
        (Step.PLAN, Event.PLAN_CREATED, 0, 0, Step.PLAN_APPROVAL),
        (Step.PLAN_APPROVAL, Event.APPROVED, 0, 0, Step.CODE),
        (Step.PLAN_APPROVAL, Event.REVISION_REQUESTED, 0, 2, Step.PLAN),
        (Step.PLAN_APPROVAL, Event.REVISION_REQUESTED, 0, 3, EXHAUSTED),
        (Step.PLAN_APPROVAL, Event.REJECTED, 0, 0, REJECTED),
        (Step.PLAN_APPROVAL, Event.EXPIRED, 0, 0, EXPIRED),
        (Step.CODE, Event.CODE_GENERATED, 0, 0, Step.WRITE_APPROVAL),
        (Step.WRITE_APPROVAL, Event.APPROVED, 0, 0, Step.VERIFY),
        (Step.WRITE_APPROVAL, Event.REVISION_REQUESTED, 0, 1, Step.CODE),
        (Step.WRITE_APPROVAL, Event.REVISION_REQUESTED, 0, 3, EXHAUSTED),
        (Step.WRITE_APPROVAL, Event.REJECTED, 0, 0, REJECTED),
        (Step.WRITE_APPROVAL, Event.EXPIRED, 0, 0, EXPIRED),
        (Step.VERIFY, Event.VERIFICATION_PASSED, 0, 0, Step.FINALIZE),
        (Step.VERIFY, Event.VERIFICATION_FAILED, 1, 0, Step.CODE),
        (Step.VERIFY, Event.VERIFICATION_FAILED, 2, 0, Step.CODE),
        (
            Step.VERIFY,
            Event.VERIFICATION_FAILED,
            3,
            0,
            Terminal(ProcessStatus.FAILED, "fixes_exhausted", "discard_worktree"),
        ),
        (Step.FINALIZE, Event.WRITTEN, 0, 0, Terminal(ProcessStatus.COMPLETED, None, None)),
        (
            Step.FINALIZE,
            Event.WRITE_FAILED,
            0,
            0,
            Terminal(ProcessStatus.FAILED, "write_failed", "discard_worktree"),
        ),
    ],
)
def test_routing_follows_the_spec_table(
    make_state: StateFactory, step: Step, event: Event, fix: int, rev: int, expected: Step | Terminal
) -> None:
    state = make_state(fix_iteration=fix, revision_iteration=rev)
    assert next_transition(step, event, state, LIMITS).target == expected


def test_unknown_transition_is_an_error(make_state: StateFactory) -> None:
    with pytest.raises(LookupError, match="plan.*Approved"):
        next_transition(Step.PLAN, Event.APPROVED, make_state(), LIMITS)


def test_guarded_pairs_always_have_exactly_one_matching_transition(make_state: StateFactory) -> None:
    pairs = {(t.source, t.event) for t in TRANSITIONS}
    for (source, event), fix, rev in product(pairs, range(6), range(6)):
        state = make_state(fix_iteration=fix, revision_iteration=rev)
        matching = [
            t for t in TRANSITIONS if (t.source, t.event) == (source, event) and t.allows(state, LIMITS)
        ]
        assert len(matching) == 1, (source, event, fix, rev)


def test_every_step_is_reachable_from_plan_and_can_exit() -> None:
    reachable, frontier = {Step.PLAN}, [Step.PLAN]
    while frontier:
        source = frontier.pop()
        for t in TRANSITIONS:
            if t.source == source and isinstance(t.target, Step) and t.target not in reachable:
                reachable.add(t.target)
                frontier.append(t.target)
    assert reachable == set(Step)
    assert all(any(t.source == s for t in TRANSITIONS) for s in Step)


def test_only_the_three_feedback_edges_are_loops() -> None:
    loops = {(t.source, t.target) for t in TRANSITIONS if t.loop}
    assert loops == {
        (Step.VERIFY, Step.CODE),
        (Step.PLAN_APPROVAL, Step.PLAN),
        (Step.WRITE_APPROVAL, Step.CODE),
    }


def test_no_code_runs_before_the_write_gate() -> None:
    """VERIFY (the only step that executes generated code) is entered only from the write gate."""
    assert {t.source for t in TRANSITIONS if t.target == Step.VERIFY} == {Step.WRITE_APPROVAL}
    assert {t.source for t in TRANSITIONS if t.target == Step.CODE} == {
        Step.PLAN_APPROVAL,
        Step.WRITE_APPROVAL,
        Step.VERIFY,
    }


def test_every_event_is_used_and_every_transition_is_labelled() -> None:
    assert {t.event for t in TRANSITIONS} == set(Event)
    assert all(t.label for t in TRANSITIONS)

import pytest

from src.metrics.recorder import Recorder
from src.orchestrator.state import ALLOWED, IllegalTransition, State, StateMachine


def make(strict=True):
    sent: list = []

    async def send_json(m):
        sent.append(m)

    rec = Recorder()
    return StateMachine(rec, send_json, strict=strict), rec, sent


def test_table_covers_every_state_and_never_allows_a_self_transition():
    assert set(ALLOWED) == set(State)
    assert all(s not in targets for s, targets in ALLOWED.items())


def test_nominal_cycle_and_barge_in_are_allowed():
    sm, _, _ = make()
    for s in (State.THINKING, State.SPEAKING, State.BARGE_IN, State.LISTENING, State.THINKING, State.LISTENING):
        assert sm.to(s)
    assert sm.state is State.LISTENING


@pytest.mark.parametrize("bad", [State.SPEAKING, State.BARGE_IN])
def test_illegal_transition_raises_in_strict_mode(bad):
    sm, _, _ = make()
    with pytest.raises(IllegalTransition, match="listening"):
        sm.to(bad)
    assert sm.state is State.LISTENING


def test_illegal_transition_is_traced_but_applied_in_production():
    sm, rec, _ = make(strict=False)
    sm.to(State.SPEAKING)
    events = [e for e, _, _ in rec.events]
    assert events == ["state_illegal", "state_change"] and sm.state is State.SPEAKING


def test_every_change_emits_a_trace_and_same_state_is_a_no_op():
    sm, rec, _ = make()
    assert sm.to(State.THINKING) and not sm.to(State.THINKING)
    assert [(e, d["from"], d["to"]) for e, _, d in rec.events] == [("state_change", "listening", "thinking")]


async def test_client_is_notified_except_for_barge_in():
    sm, _, sent = make()
    await sm.enter(State.THINKING)
    await sm.ensure_speaking()
    await sm.ensure_speaking()                 # déjà SPEAKING : rien d'envoyé
    await sm.enter(State.BARGE_IN)             # interne : le protocole client ne le connaît pas
    await sm.enter(State.LISTENING)
    assert [m["state"] for m in sent] == ["thinking", "speaking", "listening"]
    assert not sm.agent_active

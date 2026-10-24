import asyncio

from src.metrics.recorder import Recorder
from src.orchestrator.tasks import TurnTasks


async def forever():
    await asyncio.sleep(60)


async def boom():
    raise RuntimeError("panne")


async def test_cancel_all_cancels_and_waits_for_every_task():
    t = TurnTasks(Recorder())
    turn = t.spawn_turn(forever())
    fs, spec, aux = t.spawn_failsafe(forever()), t.spawn_spec(forever()), t.spawn_aux(forever())
    await asyncio.sleep(0)
    await t.cancel_all()
    assert all(x.done() and x.cancelled() for x in (turn, fs, spec, aux))
    assert t.failsafe is None and t.spec is None and t.aux == []


async def test_cancel_all_is_safe_when_idle_and_twice():
    t = TurnTasks(Recorder())
    await t.cancel_all()
    t.spawn_turn(forever())
    await t.cancel_all()
    await t.cancel_all()


async def test_task_failure_is_traced_and_exception_is_retrieved():
    rec = Recorder()
    t = TurnTasks(rec)
    loop, seen = asyncio.get_running_loop(), []
    loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
    task = t.spawn_turn(boom())
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert [(e, d["kind"]) for e, _, d in rec.events] == [("task_error", "turn")] and "RuntimeError" in rec.events[0][2]["error"]
    del task
    assert not seen


async def test_speculative_failure_is_silent_and_take_spec_hands_over_ownership():
    rec = Recorder()
    t = TurnTasks(rec)
    spec = t.spawn_spec(boom())
    await asyncio.gather(spec, return_exceptions=True)
    await asyncio.sleep(0)
    assert rec.events == [] and t.take_spec() is spec and t.spec is None


async def test_new_turn_forgets_the_previous_aux_tasks():
    t = TurnTasks(Recorder())
    t.spawn_aux(forever())
    t.spawn_turn(forever())
    assert t.aux == []
    await t.cancel_all()

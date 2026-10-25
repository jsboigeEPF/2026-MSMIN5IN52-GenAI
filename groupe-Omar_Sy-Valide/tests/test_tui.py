"""The interactive agent, driven headless with Textual's Pilot and the fake LLM (no network, no terminal)."""

import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from rich.console import Console
from textual.pilot import Pilot
from textual.widgets import Input

from src.models.state import ProcessStatus
from src.process.engine import DecisionRequest, StartRequest
from src.runtime import open_runner
from src.tui import render
from src.tui.app import OmarApp, find_repo
from tests.conftest import BUGGY_CALC, FIXED_CALC, StateFactory, git

PLAN = {"target_file": "calc.py", "steps": ["use + in add()"]}
PATCH = {"new_content": FIXED_CALC, "rationale": "use +"}


def script(tmp_path: Path, planner: int = 1, coder: int = 1) -> Path:
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"planner": [PLAN] * planner, "coder": [PATCH] * coder}))
    return path


def transcript(app: OmarApp) -> str:
    return "\n".join(app.transcript)


async def settle(app: OmarApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


async def type_task(app: OmarApp, pilot: Pilot[None], text: str) -> None:
    prompt = app.query_one("#prompt", Input)
    prompt.focus()
    prompt.value = text
    await pilot.press("enter")
    await settle(app, pilot)


async def test_task_plan_yes_diff_yes_writes_the_file_without_commit(git_repo: Path, tmp_path: Path) -> None:
    base_sha = git(git_repo, "rev-parse", "HEAD")
    app = OmarApp(git_repo, db=tmp_path / "t.db", llm_script=script(tmp_path))
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "Corrige add()")
        assert app.pending_gate() == "plan"
        assert app.query_one("#gate").has_class("visible")
        assert "Plan proposé" in transcript(app) and "use + in add()" in transcript(app)
        assert (git_repo / "calc.py").read_text() == BUGGY_CALC

        await pilot.press("o")
        await settle(app, pilot)
        assert app.pending_gate() == "write"
        assert "+    return a + b" in transcript(app)
        assert (git_repo / "calc.py").read_text() == BUGGY_CALC  # a diff, not a write

        await pilot.press("o")
        await settle(app, pilot)
        assert app.state is not None and app.state.status is ProcessStatus.COMPLETED
        assert "écrit dans ton dossier (non commité)" in transcript(app)
        assert not app.query_one("#gate").has_class("visible")

    assert (git_repo / "calc.py").read_text() == FIXED_CALC
    assert git(git_repo, "rev-parse", "HEAD") == base_sha
    assert git(git_repo, "status", "--porcelain") == "M calc.py"


async def test_refine_sends_the_comment_and_shows_a_new_plan(git_repo: Path, tmp_path: Path) -> None:
    app = OmarApp(git_repo, db=tmp_path / "t.db", llm_script=script(tmp_path, planner=2))
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "Corrige add()")
        await pilot.press("a")
        assert app.refining and app.query_one("#prompt", Input).has_focus
        await type_task(app, pilot, "garde les annotations de type")

        assert app.pending_gate() == "plan" and not app.refining
        assert app.state is not None and app.state.revision_iteration == 1
        assert transcript(app).count("Plan proposé") == 2
        assert "Affiner : garde les annotations de type" in transcript(app)


async def test_no_rejects_and_leaves_the_file_untouched(git_repo: Path, tmp_path: Path) -> None:
    app = OmarApp(git_repo, db=tmp_path / "t.db", llm_script=script(tmp_path))
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "Corrige add()")
        await pilot.click("#reject")
        await settle(app, pilot)
        assert app.state is not None and app.state.status is ProcessStatus.REJECTED
        assert "refusé. Aucun fichier modifié." in transcript(app)
    assert (git_repo / "calc.py").read_text() == BUGGY_CALC


async def test_undo_and_runs_commands(git_repo: Path, tmp_path: Path) -> None:
    app = OmarApp(git_repo, db=tmp_path / "t.db", llm_script=script(tmp_path))
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "Corrige add()")
        await pilot.press("o")
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)
        assert (git_repo / "calc.py").read_text() == FIXED_CALC

        await type_task(app, pilot, "/undo")
        assert (git_repo / "calc.py").read_text() == BUGGY_CALC
        assert "restauré" in transcript(app)
        await type_task(app, pilot, "/runs")
        assert "Derniers runs" in transcript(app) and "COMPLETED" in transcript(app)


async def test_a_new_task_waits_for_the_pending_answer(git_repo: Path, tmp_path: Path) -> None:
    app = OmarApp(git_repo, db=tmp_path / "t.db", llm_script=script(tmp_path))
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "Corrige add()")
        await type_task(app, pilot, "autre chose")
        assert "réponds d'abord" in transcript(app)
        assert app.state is not None and app.state.user_prompt == "Corrige add()"


async def test_resume_picks_up_a_run_waiting_at_a_gate(git_repo: Path, tmp_path: Path) -> None:
    db, mock = tmp_path / "t.db", script(tmp_path)
    async with open_runner(db, drive=True, mock=True, script=mock) as runner:
        run_id = await runner.start(
            StartRequest(target_repo_path=str(git_repo), user_prompt="Corrige add()", run_id="r-1")
        )
        await runner.decide(run_id, DecisionRequest(action="approve", actor="alice"))

    app = OmarApp(git_repo, db=db, llm_script=mock)
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "/resume r-1")
        assert app.pending_gate() == "write" and "+    return a + b" in transcript(app)


async def test_the_agents_own_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="l'agent lui-même"):
        find_repo(Path(__file__).parent)
    app = OmarApp(Path(__file__).parent, db=tmp_path / "t.db", llm_script=script(tmp_path))
    async with app.run_test(size=(140, 50)) as pilot:
        assert app.repo is None and "/repo <chemin>" in transcript(app)
        await type_task(app, pilot, "Corrige add()")
        assert "aucun dépôt cible" in transcript(app)


def test_rendering_escapes_model_output(make_state: StateFactory) -> None:
    state = make_state(execution_plan=["[bold red]not markup[/]"], unified_diff="+x\n")
    rendered = render.plan_panel(state).renderable
    assert "[bold red]not markup[/]" in str(rendered)
    side = render.sidebar(make_state(current_step="write_approval"), {"plan", "code"}, 0.0).plain
    assert "? Validation de l'écriture" in side and "✓ Planification" in side
    assert "write_approval -> code" in side


async def test_events_of_another_run_do_not_hijack_the_screen(git_repo: Path, tmp_path: Path) -> None:
    db, mock = tmp_path / "t.db", script(tmp_path, planner=2)
    async with open_runner(db, drive=True, mock=True, script=mock) as runner:  # an older run, about to expire
        await runner.start(StartRequest(target_repo_path=str(git_repo), user_prompt="old", run_id="old-1"))
    app = OmarApp(git_repo, db=db, llm_script=mock)
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "Corrige add()")
        assert app.runner is not None
        old = await app.runner.store.load_state("old-1")
        assert old is not None and old.approval_deadline is not None
        app.runner.clock = app.runner.ctx.clock = FrozenClock(old.approval_deadline)  # old-1 is now overdue
        await type_task(app, pilot, "/runs")
        assert app.state is not None and app.state.run_id == app.run_id != "old-1"
        assert app.pending_gate() == "plan"


async def test_a_slash_command_while_refining_is_not_sent_as_feedback(git_repo: Path, tmp_path: Path) -> None:
    app = OmarApp(git_repo, db=tmp_path / "t.db", llm_script=script(tmp_path))
    async with app.run_test(size=(140, 50)) as pilot:
        await type_task(app, pilot, "Corrige add()")
        await pilot.press("a")
        await type_task(app, pilot, "/help")
        assert not app.refining
        assert app.state is not None and app.state.revision_iteration == 0


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def test_control_characters_from_the_repository_are_shown_not_sent(make_state: StateFactory) -> None:
    state = make_state(
        target_file="calc.py",
        unified_diff="-x = 1  # \x1b[2K\x1b]52;c;evil\x07\n+x = 2\n",
        execution_plan=["step \x1b[8mhidden"],
    )
    for panel in (render.diff_panel(state), render.plan_panel(state)):
        out = io.StringIO()
        Console(file=out, width=120, force_terminal=True).print(panel)
        assert "\x1b[2K" not in out.getvalue() and "\x1b]52" not in out.getvalue()
        assert "\x1b[8mhidden" not in out.getvalue()
    assert render.printable("a\x1bb\r\nc") == "a\\x1bb\nc"

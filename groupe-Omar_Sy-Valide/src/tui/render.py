"""Rich renderables for the interactive agent. Pure functions of the run state, tested without a terminal.

LLM and tool output is always wrapped in `Text` (never parsed as console markup), so a model cannot inject
styles or links into the interface.
"""

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from src.models.state import AgentProcessState, ProcessStatus, VerificationResult
from src.process.graph import GATES, TRANSITIONS, Step

STEP_LABELS = {
    Step.PLAN: "Planification",
    Step.PLAN_APPROVAL: "Validation du plan",
    Step.CODE: "Génération du code",
    Step.WRITE_APPROVAL: "Validation de l'écriture",
    Step.VERIFY: "Tests (pytest + ruff)",
    Step.FINALIZE: "Écriture du fichier",
}
GATE_QUESTIONS = {
    "plan": "Valider ce plan ?",
    "write": "Écrire ce fichier puis lancer les tests ?",
}
TERMINAL_REASONS = {
    "human_rejected": "refusé",
    "max_iterations": "trop de demandes d'affinage",
    "approval_expired": "validation expirée",
    "fixes_exhausted": "les tests échouent encore après les corrections automatiques",
    "write_failed": "écriture refusée",
    "budget_exceeded": "budget dépassé",
    "invalid_llm_output": "réponse du modèle invalide",
    "sandbox_unavailable": "exécution des tests impossible",
    "llm_error": "erreur du modèle",
}


def user_message(text: str) -> Panel:
    return Panel(Text(text), title="Toi", title_align="left", border_style="cyan")


def progress_line(step: Step) -> Text:
    return Text.assemble(("  ⠿ ", "magenta"), (f"{STEP_LABELS[step]}…", "dim"))


def plan_panel(state: AgentProcessState) -> Panel:
    lines = Text()
    lines.append("Fichier : ", style="bold")
    lines.append(state.target_file or "?", style="bold yellow")
    for i, step in enumerate(state.execution_plan or [], 1):
        lines.append(f"\n {i}. ", style="bold")
        lines.append(step)
    return Panel(lines, title="Plan proposé", title_align="left", border_style="yellow")


def diff_panel(state: AgentProcessState) -> Panel:
    parts: list[RenderableType] = []
    v = state.verification
    if v is not None and not (v.pytest_passed and v.linter_passed):
        parts.append(Text("La tentative précédente a échoué aux tests ; voici la correction :", style="red"))
        if v.error_summary:
            parts.append(Text(v.error_summary, style="dim"))
    diff = state.unified_diff or ""
    if diff.strip():
        parts.append(Syntax(diff, "diff", theme="ansi_dark", word_wrap=True, background_color="default"))
    else:
        parts.append(Text("(aucun changement proposé)", style="dim"))
    title = f"Modification proposée — {state.target_file}"
    return Panel(Group(*parts), title=title, title_align="left", border_style="yellow")


def verification_text(v: VerificationResult) -> Text:
    def verdict(name: str, ok: bool) -> Text:
        return Text.assemble((" ✔ " if ok else " ✘ ", "green" if ok else "red"), name)

    return Text.assemble(verdict("pytest", v.pytest_passed), "  ", verdict("ruff", v.linter_passed))


def outcome_panel(state: AgentProcessState, cost_eur: float) -> Panel:
    cost = f"coût du run : {cost_eur:.5f} €"
    if state.status is ProcessStatus.COMPLETED:
        body = Text.assemble(
            ("✔ ", "bold green"),
            (state.target_file or "", "bold"),
            " écrit dans ton dossier (non commité).\n",
            ("/undo", "bold"),
            " pour annuler, ",
            ("git diff", "bold"),
            " pour relire.\n",
            (cost, "dim"),
        )
        return Panel(body, title="Terminé", title_align="left", border_style="green")
    reason = TERMINAL_REASONS.get(state.terminal_reason or "", state.terminal_reason or state.status.value)
    body = Text.assemble(("✘ ", "bold red"), f"{reason}. Aucun fichier modifié.\n")
    if state.error_message and state.terminal_reason != "human_rejected":
        body.append(state.error_message + "\n", style="dim")
    body.append(cost, style="dim")
    return Panel(body, title=state.status.value, title_align="left", border_style="red")


def sidebar(state: AgentProcessState | None, visited: set[str], cost_eur: float) -> Text:
    """The process graph from `TRANSITIONS`: steps with the run's position, then the feedback loops."""
    out = Text()
    out.append("Processus\n", style="bold")
    finished = state is not None and state.current_step == "end"
    for step in Step:
        if state is not None and state.current_step == step.value:
            icon, style = ("⏸" if step in GATES else "▶"), "bold yellow"
        elif step.value in visited:
            icon, style = "✔", "green"
        else:
            icon, style = "·", "dim"
        out.append(f" {icon} {STEP_LABELS[step]}\n", style=style)
    if finished and state is not None:
        ok = state.status is ProcessStatus.COMPLETED
        out.append(f" {'✔' if ok else '✘'} {state.status.value}\n", style="bold green" if ok else "bold red")
    out.append("\nBoucles\n", style="bold")
    for t in TRANSITIONS:
        if t.loop and isinstance(t.target, Step):
            out.append(f" ↺ {t.source.value} → {t.target.value}\n", style="dim")
    if state is not None:
        out.append("\nRun ", style="bold")
        out.append(f"{state.run_id}\n", style="dim")
        out.append(f" affinages {state.revision_iteration} · corrections {state.fix_iteration}\n")
        out.append(f" coût {cost_eur:.5f} €\n")
    return out

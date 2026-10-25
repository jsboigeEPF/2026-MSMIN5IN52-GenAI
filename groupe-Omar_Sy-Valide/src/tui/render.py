"""Rich renderables for the interactive agent. Pure functions of the run state, tested without a terminal.

Every string that comes from the LLM, the repository or a tool goes through `printable` (control characters
shown as `\x1b`, never sent to the terminal) and is wrapped in `Text` (never parsed as console markup).
Symbols are limited to ones every terminal draws one cell wide, so the layout stays aligned.
"""

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from src.labels import STEP_LABELS, TERMINAL_REASONS
from src.models.state import AgentProcessState, ProcessStatus, VerificationResult
from src.process.graph import GATES, TRANSITIONS, Limits, Step
from src.process.steps import HumanApprovalStep
from src.safe_text import printable

GATE_QUESTIONS = {
    "plan": "Valider ce plan ?",
    "write": "Écrire ce fichier puis lancer les tests ?",
}


def gate_question(state: AgentProcessState, limits: Limits) -> Text:
    if HumanApprovalStep.write_anyway_allowed(state, limits):
        return Text(
            f"Les tests échouent encore après {limits.max_fix} corrections automatiques. "
            "Écrire quand même, affiner (a) ou abandonner (n) ?",
            style="bold red",
        )
    gate = "plan" if state.current_step == Step.PLAN_APPROVAL else "write"
    return Text(GATE_QUESTIONS[gate])


def user_message(text: str) -> Panel:
    return Panel(Text(printable(text)), title="Toi", title_align="left", border_style="cyan")


def progress_line(step: Step) -> Text:
    return Text.assemble(("  > ", "magenta"), (f"{STEP_LABELS[step]}...", "dim"))


def plan_panel(state: AgentProcessState) -> Panel:
    lines = Text()
    lines.append("Fichier : ", style="bold")
    lines.append(printable(state.target_file or "?"), style="bold yellow")
    for i, step in enumerate(state.execution_plan or [], 1):
        lines.append(f"\n {i}. ", style="bold")
        lines.append(printable(step))
    return Panel(lines, title="Plan proposé", title_align="left", border_style="yellow")


def diff_panel(state: AgentProcessState) -> Panel:
    parts: list[RenderableType] = []
    v = state.verification
    if v is not None and not (v.pytest_passed and v.linter_passed):
        parts.append(Text("La tentative précédente a échoué aux tests ; voici la correction :", style="red"))
        if v.error_summary:
            parts.append(Text(printable(v.error_summary), style="dim"))
    diff = printable(state.unified_diff or "")
    if diff.strip():
        parts.append(Syntax(diff, "diff", theme="ansi_dark", word_wrap=True, background_color="default"))
    else:
        parts.append(Text("(aucun changement proposé)", style="dim"))
    title = Text(f"Modification proposée - {printable(state.target_file or '?')}")
    return Panel(Group(*parts), title=title, title_align="left", border_style="yellow")


def verification_text(v: VerificationResult) -> Text:
    def verdict(name: str, ok: bool) -> Text:
        return Text.assemble((" ✓ " if ok else " ✗ ", "green" if ok else "red"), name)

    return Text.assemble(
        verdict(v.tests_label, v.pytest_passed), "  ", verdict(v.lint_label, v.linter_passed)
    )


def outcome_panel(state: AgentProcessState, cost_eur: float) -> Panel:
    cost = f"coût du run : {cost_eur:.5f} €"
    if state.status is ProcessStatus.COMPLETED:
        failing = state.terminal_reason == "written_despite_failing_tests"
        body = Text.assemble(
            ("! " if failing else "✓ ", "bold yellow" if failing else "bold green"),
            (printable(state.target_file or ""), "bold"),
            " écrit dans ton dossier (non commité)",
            (", alors que les tests échouent" if failing else "", "bold yellow"),
            ".\n",
            ("/undo", "bold"),
            " pour annuler, ",
            ("git diff", "bold"),
            " pour relire.\n",
            (cost, "dim"),
        )
        return Panel(body, title="Terminé", title_align="left", border_style="yellow" if failing else "green")
    reason = TERMINAL_REASONS.get(state.terminal_reason or "", state.terminal_reason or state.status.value)
    body = Text.assemble(("✗ ", "bold red"), f"{reason}. Aucun fichier modifié.\n")
    if state.error_message and state.terminal_reason != "human_rejected":
        body.append(printable(state.error_message) + "\n", style="dim")
    body.append(cost, style="dim")
    return Panel(body, title=state.status.value, title_align="left", border_style="red")


def sidebar(state: AgentProcessState | None, visited: set[str], cost_eur: float) -> Text:
    """The process graph from `TRANSITIONS`: steps with the run's position, then the feedback loops."""
    out = Text()
    out.append("Processus\n", style="bold")
    finished = state is not None and state.current_step == "end"
    for step in Step:
        if state is not None and state.current_step == step.value:
            icon, style = ("?" if step in GATES else ">"), "bold yellow"
        elif step.value in visited:
            icon, style = "✓", "green"
        else:
            icon, style = "·", "dim"
        out.append(f" {icon} {STEP_LABELS[step]}\n", style=style)
    if finished and state is not None:
        ok = state.status is ProcessStatus.COMPLETED
        out.append(f" {'✓' if ok else '✗'} {state.status.value}\n", style="bold green" if ok else "bold red")
    out.append("\nBoucles\n", style="bold")
    for t in TRANSITIONS:
        if t.loop and isinstance(t.target, Step):
            out.append(f" {t.source.value} -> {t.target.value}\n", style="dim")
    if state is not None:
        out.append("\nRun ", style="bold")
        out.append(f"{state.run_id}\n", style="dim")
        out.append(f" affinages {state.revision_iteration} · corrections {state.fix_iteration}\n")
        out.append(f" coût {cost_eur:.5f} €\n")
    return out


STREAM_TITLES = {
    "planner": "Omar prépare le plan",
    "coder": "Omar écrit le code",
    "summarizer": "Omar résume l'erreur",
}
_JSON_ESCAPES = (("\\\\", "\x00"), ("\\n", "\n"), ("\\t", "    "), ('\\"', '"'), ("\x00", "\\"))


def _readable(raw: str) -> str:
    """The reply is a JSON object being generated: show its strings as text (code keeps its lines)."""
    for escaped, plain in _JSON_ESCAPES:
        raw = raw.replace(escaped, plain)
    return raw


def live_panel(service_id: str, text: str, reasoning: str, max_lines: int = 30) -> Panel:
    """The reply as it streams in: the tail of the model's reasoning (thinking models), then of its answer."""
    parts: list[RenderableType] = []
    if reasoning:
        thought = printable(reasoning).splitlines()[-4:]
        parts.append(Text("\n".join(thought), style="dim italic"))
    body = printable(_readable(text)).splitlines()[-max_lines:]
    parts.append(Text("\n".join(body) or "...", style="default"))
    title = f"{STREAM_TITLES.get(service_id, service_id)} (en direct)"
    return Panel(Group(*parts), title=Text(title), title_align="left", border_style="magenta")

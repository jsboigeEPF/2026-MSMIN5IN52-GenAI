"""Replay a run as the conversation it was: task, plans, decisions, diffs, tests, outcome (for /resume).

The run state only keeps the latest plan and diff; earlier ones come from the LLM cache, which holds every
validated reply by (step, iteration). Pure function of stored data, tested without a terminal.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from rich.console import RenderableType
from rich.text import Text

from src.labels import TERMINAL_REASONS
from src.models.state import AgentProcessState, ApprovalDecision, CodePatch, ErrorSummary, PlanOutput
from src.process.graph import Step
from src.safe_text import printable
from src.storage.sqlite_store import EventRecord
from src.tools.patch import PatchEngine
from src.tui import render

_DECISIONS = {"approve": "Oui", "reject": "Non", "write_anyway": "Écrire quand même"}
_EARLIEST = datetime(1970, 1, 1, tzinfo=UTC)


def _reply(responses: dict[tuple[str, int], str], step: str, iteration: int, model: type[Any]) -> Any:
    raw = responses.get((step, iteration))
    if raw is None:
        return None
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        return None


def _decision(d: ApprovalDecision) -> Text:
    label = f"Affiner : {d.comment}" if d.action == "revise" else _DECISIONS[d.action]
    return Text(f"> {printable(label)}", style="bold cyan")


def conversation(
    state: AgentProcessState,
    events: list[EventRecord],
    approvals: list[ApprovalDecision],
    responses: dict[tuple[str, int], str],
    cost_eur: float,
) -> list[RenderableType]:
    """What the screen showed during the run, in order. The pending gate, if any, is left to the caller."""
    timeline: list[tuple[datetime, int, RenderableType]] = []
    original = state.original_file_content or ""
    target = state.target_file or "?"
    for order, d in enumerate(approvals):
        timeline.append((d.decided_at, order, _decision(d)))
    for e in events:
        key = len(approvals) + e.id  # after a decision made at the same instant
        done = e.event_type == "step_completed"
        if e.event_type == "run_started":
            timeline.append((e.ts, key, render.user_message(state.user_prompt)))
        elif done and e.step == Step.PLAN:
            plan = _reply(responses, Step.PLAN, e.iteration, PlanOutput)
            if plan is not None:
                shown = state.model_copy(
                    update={"target_file": plan.target_file, "execution_plan": plan.steps}
                )
                timeline.append((e.ts, key, render.plan_panel(shown)))
        elif done and e.step == Step.CODE:
            patch = _reply(responses, Step.CODE, e.iteration, CodePatch)
            if patch is not None:
                diff = PatchEngine.diff(target, original, patch.new_content)
                shown = state.model_copy(update={"unified_diff": diff, "verification": None})
                timeline.append((e.ts, key, render.diff_panel(shown)))
        elif done and e.step == Step.VERIFY:
            passed = (e.payload or {}).get("event") == "VerificationPassed"
            line = Text("  Tests et lint : ", style="dim")
            line.append("réussis" if passed else "en échec", style="green" if passed else "red")
            summary = _reply(responses, Step.VERIFY, e.iteration, ErrorSummary)
            if summary is not None and not passed:
                line.append(f"\n  {printable(summary.summary)}", style="dim")
            timeline.append((e.ts, key, line))
        elif e.event_type == "step_failed":
            reason = str((e.payload or {}).get("reason", ""))
            timeline.append((e.ts, key, Text(f"✗ {TERMINAL_REASONS.get(reason, reason)}", style="red")))
        elif e.event_type == "run_finished":
            timeline.append((e.ts, key, render.outcome_panel(state, cost_eur)))
    return [item for _, _, item in sorted(timeline, key=lambda t: (t[0] or _EARLIEST, t[1]))]

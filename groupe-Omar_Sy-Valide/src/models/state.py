"""Domain models (SPEC §3).

Additions to the spec: `scenario` label, `TERMINAL_STATUSES`, `ErrorSummary`, and in the state
`loop_iteration` (one per pass through the graph, keys checkpoints and the LLM cache), `awaiting_since`
(start of the human wait), `file_locked` (target file given by the user, not chosen by the planner) and
`undone` (the written file was restored).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.safe_text import control_chars_error


class ProcessStatus(StrEnum):
    INITIALIZED = "INITIALIZED"
    PLANNING = "PLANNING"
    CODING = "CODING"
    VERIFYING = "VERIFYING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    FINALIZING = "FINALIZING"
    # terminal
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


TERMINAL_STATUSES = frozenset(
    {ProcessStatus.COMPLETED, ProcessStatus.REJECTED, ProcessStatus.EXPIRED, ProcessStatus.FAILED}
)


class StepMetrics(BaseModel):
    step_name: str
    iteration: int
    model_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    compute_ms: float = 0.0  # machine time only
    wait_ms: float = 0.0  # human waiting time (approval step only)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    status: str


class VerificationResult(BaseModel):
    # Field names come from the Python-only version; the labels say which tools actually ran.
    pytest_passed: bool  # the tests (pytest, npm test, go test...)
    pytest_output: str
    linter_passed: bool  # the linter (ruff, npm run lint, go vet...)
    linter_output: str
    error_summary: str | None = None
    tests_label: str = "pytest"
    lint_label: str = "ruff"


def _reject_control_chars(value: str) -> str:
    """LLM output is shown to a human before it may run: refuse what could hide part of it on screen."""
    if reason := control_chars_error(value):
        raise ValueError(reason)
    return value


class CodePatch(BaseModel):  # structured LLM output of Step 2
    new_content: str
    rationale: str

    _clean = field_validator("new_content", "rationale")(_reject_control_chars)


class PlanOutput(BaseModel):  # structured LLM output of Step 1
    target_file: str  # repository-relative path of the one file to change, chosen by the planner
    steps: list[str]

    _clean_file = field_validator("target_file")(_reject_control_chars)

    @field_validator("steps")
    @classmethod
    def _clean_steps(cls, steps: list[str]) -> list[str]:
        return [_reject_control_chars(step) for step in steps]


class ErrorSummary(BaseModel):  # structured LLM output of Step 3 (summarizer)
    summary: str


Gate = Literal["plan", "write"]


class ApprovalDecision(BaseModel):  # append-only audit record
    gate: Gate
    action: Literal["approve", "revise", "reject", "write_anyway"]
    actor: str
    comment: str | None = None
    decided_at: datetime
    iteration: int


class CompensationRecord(BaseModel):
    action: Literal["discard_worktree"]
    done: bool
    detail: str | None = None
    at: datetime


class AgentProcessState(BaseModel):
    run_id: str
    scenario: str | None = None  # evaluation label, groups runs in the report
    status: ProcessStatus = ProcessStatus.INITIALIZED
    terminal_reason: str | None = None  # e.g. "human_rejected", "approval_expired",
    # "commit_failed", "post_commit_check_failed",
    # "budget_exceeded", "max_iterations"
    current_step: str = "init"
    target_repo_path: str
    base_branch: str
    worktree_path: str | None = None
    run_branch: str | None = None
    target_file: str | None = None  # chosen by the planner unless given by the user
    file_locked: bool = False  # True when the user gave the target file
    user_prompt: str
    original_file_content: str | None = None
    target_existed: bool = True  # False when the run creates the file (undo then deletes it)
    execution_plan: list[str] | None = None
    generated_code: str | None = None
    unified_diff: str | None = None
    verification: VerificationResult | None = None
    revision_iteration: int = 0  # loops triggered by human "revise"
    fix_iteration: int = 0  # loops triggered by failed verification
    loop_iteration: int = 0  # +1 on every feedback edge (verify->code, approval->plan)
    human_feedback: str | None = None
    awaiting_since: datetime | None = None
    approval_deadline: datetime | None = None
    undone: bool = False
    compensation: CompensationRecord | None = None
    metrics: list[StepMetrics] = Field(default_factory=list[StepMetrics])
    error_message: str | None = None

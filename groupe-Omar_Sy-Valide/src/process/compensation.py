"""Terminal handler that undoes a run's side effects (SPEC §5.4). Not a graph step.

The user's working tree is only written by a successful finalization, so every other ending leaves only the
run's worktree and branch to discard. Compensation never raises: a failure is recorded with `done=False` so
the run still reaches its terminal status.
"""

from pathlib import Path
from typing import Literal

from src.clock import Clock
from src.models.state import AgentProcessState, CompensationRecord
from src.tools.git_ops import GitError
from src.tools.workspace import WorkspaceManager


class CompensationHandler:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def run(self, state: AgentProcessState, action: Literal["discard_worktree"]) -> CompensationRecord:
        try:
            WorkspaceManager(Path(state.target_repo_path), state.base_branch).discard(state.run_id)
            done, detail = True, "worktree and run branch discarded"
        except (GitError, ValueError) as exc:
            done, detail = False, f"discard failed: {exc}"
        return CompensationRecord(action=action, done=done, detail=detail, at=self.clock.now())

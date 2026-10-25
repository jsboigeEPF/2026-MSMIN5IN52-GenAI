"""Terminal handler that undoes a run's side effects (SPEC §5.4, step 5). Not a graph step.

Before the merge only the run's worktree and branch exist, so they are discarded. After the merge the
commit is reverted on the base branch first. Compensation never raises: a failure is recorded with
`done=False` so the run still reaches its terminal status.
"""

from pathlib import Path
from typing import Literal

from src.clock import Clock
from src.models.state import AgentProcessState, CompensationRecord
from src.tools.git_ops import GitError, GitOps, run_git
from src.tools.workspace import WorkspaceManager


class CompensationHandler:
    def __init__(self, git: GitOps, clock: Clock) -> None:
        self.git = git
        self.clock = clock

    def run(
        self, state: AgentProcessState, action: Literal["discard_worktree", "git_revert"]
    ) -> CompensationRecord:
        repo = Path(state.target_repo_path)
        details: list[str] = []
        done = True
        if action == "git_revert":
            done = self._revert(state, repo, details)
        try:
            WorkspaceManager(repo, state.base_branch).discard(state.run_id)
            details.append("worktree and run branch discarded")
        except (GitError, ValueError) as exc:
            done = False
            details.append(f"discard failed: {exc}")
        return CompensationRecord(action=action, done=done, detail="; ".join(details), at=self.clock.now())

    def _revert(self, state: AgentProcessState, repo: Path, details: list[str]) -> bool:
        sha = state.git_commit_hash
        if sha is None:
            details.append("nothing merged, nothing to revert")
            return True
        try:
            current = run_git(repo, "branch", "--show-current")
            if current != state.base_branch:
                raise GitError(f"'{state.base_branch}' is no longer checked out (found '{current}')")
            revert_sha = self.git.revert(repo, sha)
        except GitError as exc:
            details.append(f"revert of {sha[:12]} failed: {exc}")
            return False
        details.append(f"reverted {sha[:12]} with {revert_sha[:12]}")
        return True

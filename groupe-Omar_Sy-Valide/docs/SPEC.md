# SYSTEM SPECIFICATION: Mini-OpenCode (Agentic Coding Process with HITL)

> Repository note: in this fork, the project root described below as `mini-opencode/` is the group folder
> `groupe-Omar_Sy-Valide/` (course rule: AGENTS.md at the repository root). The Claude Code project root
> (plugins, `.claude/settings.json`) is the repository root; a shared copy of the settings is committed in
> `groupe-Omar_Sy-Valide/.claude/settings.json`.

## 1. ROLE, OBJECTIVE & DEVELOPMENT WORKFLOW RULES
You are a senior AI systems engineer. Your objective is to implement "Mini-OpenCode", a deterministic, stateful and observable coding agent inspired by OpenCode, built with **Python 3.11+** and **Microsoft Semantic Kernel**.

Core design principle: **the process graph is explicit and deterministic.** Transitions are code, never decided by an LLM. LLMs are only called *inside* steps and return structured outputs; tools are plain deterministic Python called by the step code, never by the LLM.

### Mandatory workflow rules
1. **Phase gate:** Follow the phases in Section 10 in order. At the end of each phase: run `pytest`, print a short summary (files created, tests passing, open questions), then **STOP and wait for the user to type "continue"**. Never start the next phase on your own.
2. **Test-first, mock-first:** No placeholder code (`# TODO`, bare `pass`). Every step must run and be tested with `--mock-llm` before any live OpenRouter call.
3. **Keep it minimal:** complete and runnable, but small. No speculative abstractions, no features not listed here.
4. **API grounding:** Before using `semantic_kernel` APIs, check the installed version and its actual imports (use a documentation tool such as Context7 MCP if available, otherwise inspect the installed package). Never guess an import.
5. **Claude Code practices:** Section 11 (setup, plugins, git conventions, commit attribution) is mandatory and starts before Phase 0.

---

## 2. TECH STACK & OPENROUTER ROUTING
- **Language:** Python 3.11+, strict typing, `pydantic>=2`, `pydantic-settings`, `asyncio`.
- **LLM layer:** `semantic-kernel` chat completion services + function invocation filters (for LLM tracing).
- **Process engine:** see Section 5 (SK Process Framework if it passes the Phase 0 spike, otherwise the in-house engine; both driven by the same transition table).
- **OpenRouter integration (`src/kernel_factory.py`):** SK `OpenAIChatCompletion` with a custom `AsyncOpenAI` client:
  - `base_url = settings.OPENROUTER_BASE_URL` (default `https://openrouter.ai/api/v1`)
  - `api_key = settings.OPENROUTER_API_KEY`
  - `default_headers = {"X-Title": "Mini-OpenCode-B1"}`
- **Multi-model routing (registered by `service_id`):**
  1. `planner` – Step 1 (Plan).
  2. `coder` – Step 2 (Code generation / fixes).
  3. `summarizer` – Step 3, condenses pytest/linter output when verification fails.
  Model slugs live **only** in `.env`. Candidate models (Qwen, DeepSeek, GLM families…) must be **checked on openrouter.ai/models** (exact slug, price, structured-output support) before use. Do not hardcode model names anywhere in code or tests.
- **Cost & budget:** read token usage (and cost, if OpenRouter returns it) from each response; store `cost_usd`, convert with configurable `USD_EUR_RATE`. Enforce `MAX_COST_EUR_PER_RUN` (run → `FAILED`, reason `budget_exceeded`) and report the global total against the 10 EUR budget.
- **Persistence:** SQLite (`aiosqlite`).
- **Observability:** `opentelemetry-api`, `opentelemetry-sdk` (OTLP exporter + console exporter).
- **Sandbox:** see Section 4.
- **Mock mode:** `--mock-llm` → a scripted fake LLM (deterministic responses per scenario, call counter for assertions). Zero network calls.
- **UI:** `typer` CLI (MVP) + `streamlit` dashboard (Phase 5).
- **Clock:** a `Clock` protocol (`now() -> datetime`) injected everywhere time is read; `SystemClock` in prod, `FakeClock` in tests. Never call `datetime.now()` directly.

---

## 3. DOMAIN DATA MODELS (Pydantic v2) — `src/models/state.py`

```python
from enum import Enum
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

class ProcessStatus(str, Enum):
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

class StepMetrics(BaseModel):
    step_name: str
    iteration: int
    model_id: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    compute_ms: float = 0.0       # machine time only
    wait_ms: float = 0.0          # human waiting time (approval step only)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    status: str

class VerificationResult(BaseModel):
    pytest_passed: bool
    pytest_output: str
    linter_passed: bool
    linter_output: str
    error_summary: Optional[str] = None

class CodePatch(BaseModel):          # structured LLM output of Step 2
    new_content: str
    rationale: str

class PlanOutput(BaseModel):         # structured LLM output of Step 1
    target_file: str                 # the one file to change, chosen by the planner
    steps: list[str]

class ApprovalDecision(BaseModel):   # append-only audit record
    gate: Literal["plan", "write"]
    action: Literal["approve", "revise", "reject"]
    actor: str
    comment: Optional[str] = None
    decided_at: datetime
    iteration: int

class CompensationRecord(BaseModel):
    action: Literal["discard_worktree"]
    done: bool
    detail: Optional[str] = None
    at: datetime

class AgentProcessState(BaseModel):
    run_id: str
    status: ProcessStatus = ProcessStatus.INITIALIZED
    terminal_reason: Optional[str] = None   # e.g. "human_rejected", "approval_expired",
                                            # "write_failed", "fixes_exhausted",
                                            # "budget_exceeded", "max_iterations"
    current_step: str = "init"
    target_repo_path: str
    base_branch: str
    worktree_path: Optional[str] = None
    run_branch: Optional[str] = None
    target_file: Optional[str] = None   # chosen by the planner unless given by the user
    file_locked: bool = False           # True when the user gave the target file
    user_prompt: str
    original_file_content: Optional[str] = None
    execution_plan: Optional[list[str]] = None
    generated_code: Optional[str] = None
    unified_diff: Optional[str] = None
    verification: Optional[VerificationResult] = None
    revision_iteration: int = 0     # loops triggered by human "revise"
    fix_iteration: int = 0          # loops triggered by failed verification
    human_feedback: Optional[str] = None
    approval_deadline: Optional[datetime] = None
    undone: bool = False                # the written file was restored (`undo`)
    compensation: Optional[CompensationRecord] = None
    metrics: list[StepMetrics] = Field(default_factory=list)
    error_message: Optional[str] = None
```

---

## 4. DETERMINISTIC TOOLS — `src/tools/`
Plain Python classes, unit-tested, **called by step code only** (no LLM tool calling).

1. **`WorkspaceManager`** (`workspace.py`)
   - `create(run_id) -> (worktree_path, run_branch)`: `git worktree add <repo>/.mini-opencode/runs/<run_id> -b mini-opencode/<run_id> <base_branch>`. Idempotent: if it already exists, reuse it.
   - `discard(run_id)`: remove the worktree and delete the run branch. Idempotent.
   - The user's working tree and base branch are **never** modified before approval.
2. **`CodeInspector`** (`inspector.py`)
   - `read_file(path) -> str`
   - `inspect_ast(path) -> str`: classes, signatures and docstrings via `ast`, to keep prompts small.
3. **`PatchEngine`** (`patch.py`)
   - `diff(file, old, new) -> unified_diff`: pure, shown at the write gate before anything is written.
   - `apply(root, file, new_content)`: writes under `root` (the run worktree, then, once approved and verified, the user's working tree). `resolve` refuses absolute paths, `..` escapes and `.git/` / `.mini-opencode/`.
4. **`plan_checks`** (`checks.py`): the tests and linter for the target file, from the project around it: pytest + ruff for `.py`; the nearest `package.json`'s `test` / `lint` npm scripts (its `node_modules` shared with the worktree); `go test` / `go vet`; `cargo test`; nothing (said explicitly) otherwise. A `.omar.toml` (`[checks] cwd/test/lint`) overrides the detection.
5. **`SandboxRunner`** (`sandbox.py`)
   - `run_pytest(worktree_path)` and `run_linter(worktree_path, file)` (`ruff`), each returning `(passed, output)`.
   - `SANDBOX_MODE=subprocess` (default): `cwd=worktree`, stripped environment (no API keys), `timeout=SANDBOX_TIMEOUT_SEC`. Safe as a default because only human-approved code ever runs (write gate before verify).
   - `SANDBOX_MODE=docker` (optional): `docker run --rm --network none -v <worktree>:/work:ro -w /work <image> ...`; fails closed if Docker or the image is missing.
   - A timeout counts as a failure with an explicit message.
6. **git helpers** (`git_ops.py`)
   - `run_git(cwd, *args)` and `list_files(repo)` (tracked + untracked, not ignored), used by the planner to choose the file. No commit: the agent writes the approved file and leaves committing to the user.

---

## 5. PROCESS ENGINE & GRAPH — `src/process/`

### 5.1 Single source of truth: the transition table (`graph.py`)
The graph is declared once as data (`(from_step, event) -> to_step`) and used by the runner, the tests and the dashboard (to draw the graph and highlight the current step).

Two human gates: the **plan** (before any code is generated) and the **write** (before the proposed file is written and tested). No generated code runs before a human has read it.

```
plan ──PlanCreated──▶ plan_approval
plan_approval ──Approved──▶ code
plan_approval ──RevisionRequested [revision_iteration < MAX_REVISIONS]──▶ plan   (human_feedback injected)
code ──CodeGenerated──▶ write_approval            (diff computed, nothing written)
write_approval ──Approved──▶ verify                (approve always means "write in the worktree and test")
write_approval ──WriteAnywayRequested──▶ finalize  (own decision, accepted only once fixes ran out: the code shown is the one that failed)
write_approval ──RevisionRequested [revision_iteration < MAX_REVISIONS]──▶ code  (human_feedback injected)
<either gate> ──RevisionRequested [revision_iteration ≥ MAX_REVISIONS]──▶ compensate (REJECTED, "max_iterations")
<either gate> ──Rejected──▶ compensate (REJECTED, "human_rejected")
<either gate> ──Expired──▶ compensate (EXPIRED, "approval_expired")
verify ──VerificationPassed──▶ finalize           (approved file written in the worktree, then tested)
verify ──VerificationFailed [fix_iteration < MAX_FIX]──▶ code   (auto-fix: the fix goes back to the write gate)
verify ──VerificationFailed [fix_iteration ≥ MAX_FIX]──▶ write_approval   (the human decides: refine, reject, write anyway)
finalize ──Written [tests pass]──▶ COMPLETED      (file written in the user's working tree, not committed)
finalize ──Written [tests fail]──▶ COMPLETED ("written_despite_failing_tests")
finalize ──WriteFailed──▶ compensate (FAILED, "write_failed")   (file edited meanwhile: nothing overwritten)
```

### 5.2 Engine choice (decided in Phase 0)
- **Option A – SK Process Framework**, only if the Phase 0 spike proves, on the installed version, that a process can be (1) suspended at the approval step and (2) restarted from a persisted state *at a given step* without re-running previous steps.
- **Option B – in-house engine (`engine.py`)** otherwise: an async loop that reads the transition table, runs the current step, persists, emits the next event. Semantic Kernel remains used for LLM services and filters.
- Both options expose the same `ProcessRunner` interface: `start(request) -> run_id`, `resume(run_id)`, `decide(run_id, decision)`, `sweep_expired()`.

### 5.3 Checkpointing & idempotence (mandatory)
- Every step writes `step_started` and `step_completed` events, and the full `AgentProcessState` is persisted after each step.
- On resume, the runner restarts at the first step without `step_completed` for the current iteration.
- Every LLM call goes through an **LLM cache** keyed by `(run_id, step, iteration, prompt_hash)`: a step re-run after a crash replays the cached response instead of paying again.
- Tools are idempotent (worktree reuse, patch overwrites the file, revert checks whether it has already been done).

### 5.4 The steps (`steps.py`)

**Step 1 – `PlanStep`** (`planner`)
- Input: `user_prompt`, the repository file listing and Python outlines (or the user's `target_file`), `human_feedback` (if refining).
- Creates/reuses the worktree, asks for a `PlanOutput` (structured JSON, validated by Pydantic; one retry on validation error, then `FAILED`). An unsafe `target_file` is an invalid output.
- Reads the original content from the **user's working tree**. Emits `PlanCreated`.

**Plan gate – `HumanApprovalStep` (`plan`)**: approve → code, refine (comment) → plan, reject.

**Step 2 – `CodeGenerationStep`** (`coder`)
- Input: plan, original content, previous proposal, `verification.error_summary` when fixing, `human_feedback`.
- Asks for a `CodePatch` (structured output); the step computes `unified_diff` with `PatchEngine.diff`. **Nothing is written.**
- Emits `CodeGenerated`.

**Write gate – `HumanApprovalStep` (`write`)**: approve → write and verify, refine (comment) → code, reject.

Both gates: set `AWAITING_APPROVAL` and `approval_deadline = clock.now() + APPROVAL_TIMEOUT_SEC`, persist, and **suspend** (the runner returns). Decisions arrive via the interactive agent, the CLI or the dashboard, are stored as an `ApprovalDecision` with their gate (append-only) and resume the run. A human revision clears the previous verification result. **Expiry:** `sweep_expired()` moves overdue runs to `Expired` (called by `sweep`, at the start of `resume`/`decide`/`status`, and on each dashboard refresh); a late decision is refused. `wait_ms` = time between suspension and decision; `compute_ms` excludes it.

**Step 3 – `VerificationStep`** (tools + `summarizer`)
- Writes the approved content into the worktree, then runs `run_pytest` and `run_linter` concurrently (`asyncio.gather`).
- pytest exit code 5 (no tests collected) counts as a pass only if the user's repository has no test file at all. Ruff runs with the target repository's configuration and fails only on violations the change introduces (baseline: the original content, linted through stdin under the same path; matched on rule and message).
- On failure: `summarizer` condenses the output (in mock mode: deterministic truncation), increments `fix_iteration`. A human "refine" resets `fix_iteration` (fresh budget of `MAX_FIX` = 10 automatic fixes).
- Emits `VerificationPassed` or `VerificationFailed` (routing per table).

**Step 4 – `FinalizationStep`** (+ `CompensationHandler`)
- Checks the worktree file is byte-for-byte the approved content and that the user's file still equals the original, then writes the approved content into the user's working tree. **No commit.**
- Success → `COMPLETED`, worktree removed. `undo(run_id)` later restores the original content if the file still holds exactly what the run wrote.
- `CompensationHandler` (terminal handler): every other ending discards the worktree and run branch (`discard_worktree`); the user's tree was never written. The status keeps the real outcome (`REJECTED` / `EXPIRED` / `FAILED`) with `terminal_reason`.

---

## 6. STORAGE — `src/storage/sqlite_store.py`
Tables:
- `runs` (run_id, status, state_json, updated_at)
- `events` – **append-only execution journal**: (id, run_id, ts, step, iteration, event_type, payload_json). This is the run log required by the MVP.
- `approvals` – append-only `ApprovalDecision` records.
- `llm_cache` – (run_id, step, iteration, prompt_hash, response_json, usage_json).
- `metrics` – one row per `StepMetrics`.

---

## 7. OBSERVABILITY — `src/observability/telemetry.py`
- One root span per run (`process.run`), one child span per step execution (`step.<name>`), one child span per LLM call (via an SK function-invocation filter).
- Attributes: `process.run_id`, `process.step.name`, `process.iteration`, `process.step.compute_ms`, `process.step.wait_ms`, `llm.provider` (`openrouter`|`mock`), `llm.service_id`, `llm.model`, `llm.tokens.prompt`, `llm.tokens.completion`, `llm.cost_usd`, `verification.pytest_passed`, `verification.linter_passed`, `approval.gate`, `approval.action`, `approval.actor`.
- `generate_evaluation_report(db_path)`:
  1. completion rate per scenario;
  2. mean and P95 **compute time** per step and per model, and human wait time reported separately;
  3. average number of fix and revision iterations per run;
  4. total tokens, cost in USD and EUR, against the 10 EUR budget.

---

## 8. TEST SUITE — `tests/`
All scenario tests run with the fake LLM and `FakeClock` (no `sleep`). The fixture builds a temporary git repo with a buggy module and its pytest suite.

Mandatory scenarios (`test_scenarios.py`):
1. `test_nominal` – the planner picks the file; plan gate → write gate → verify → `COMPLETED`; file written, **no commit**, worktree removed.
2. `test_rejection` (at each gate) – `reject` → `REJECTED`, compensation `discard_worktree`, user file unchanged, decision stored with its gate.
3. `test_timeout` – advance `FakeClock` past the deadline, call `sweep_expired()` → `EXPIRED`, worktree discarded, late `approve` refused.
4. `test_resume_while_awaiting_approval` – destroy the runner at the write gate, create a fresh one, `approve` → `COMPLETED`; fake-LLM call count proves earlier steps were not re-run.
5. `test_crash_mid_step` – fault injector raises inside `CodeGenerationStep` *after* the LLM call; fresh runner resumes at Step 2, the LLM response comes from the cache, run reaches the write gate.

Additional tests:
6. `test_refine_plan_then_refine_diff` – refine at the plan gate (feedback in the planner prompt), then at the write gate (feedback and previous proposal in the coder prompt); `MAX_REVISIONS` exceeded → `REJECTED` (`max_iterations`).
7. `test_auto_fix_loop` – first patch fails tests, the fix goes back to the write gate, then passes; exhausting `MAX_FIX` hands the failing code back to the write gate (refine, reject, or write anyway → `COMPLETED` / `written_despite_failing_tests`).
8. `test_write_conflict` – the user edits the file during the run → `FAILED` (`write_failed`), nothing overwritten.
9. `test_budget_cap` – fake usage above `MAX_COST_EUR_PER_RUN` → `FAILED` (`budget_exceeded`).
10. `test_project_without_tests_and_with_old_lint_debt` – no test suite and pre-existing lint violations → `COMPLETED`.
Plus unit tests for every tool and for the transition table.

---

## 9. PROJECT STRUCTURE

```text
mini-opencode/
├── .env.example            # OPENROUTER_API_KEY, OPENROUTER_BASE_URL, PLANNER_MODEL, CODER_MODEL,
│                           # SUMMARIZER_MODEL, USD_EUR_RATE, MAX_COST_EUR_PER_RUN, APPROVAL_TIMEOUT_SEC,
│                           # MAX_FIX, MAX_REVISIONS, SANDBOX_MODE, SANDBOX_TIMEOUT_SEC
├── .claude/settings.json   # attribution off, permissions, project-scope plugins (Section 11)
├── .githooks/commit-msg    # enforces team Co-authored-by trailers
├── .gitignore              # .env, .mini-opencode/, *.db, __pycache__
├── CLAUDE.md               # short project memory for Claude Code
├── pyproject.toml
├── README.md               # setup incl. plugin install commands and hooksPath
├── docs/SPEC.md            # this specification
├── docs/phase0_sk_spike.md # Phase 0 findings and engine decision
├── src/
│   ├── config.py
│   ├── clock.py
│   ├── kernel_factory.py
│   ├── llm/
│   │   ├── client.py       # structured-output calls, cache, cost accounting
│   │   └── fake.py         # scripted fake LLM for --mock-llm and tests
│   ├── models/state.py
│   ├── storage/sqlite_store.py
│   ├── observability/telemetry.py
│   ├── tools/
│   │   ├── workspace.py
│   │   ├── inspector.py
│   │   ├── patch.py
│   │   ├── sandbox.py
│   │   └── git_ops.py
│   ├── process/
│   │   ├── graph.py        # transition table
│   │   ├── steps.py
│   │   ├── compensation.py
│   │   └── engine.py       # ProcessRunner (SK-backed or in-house)
│   ├── cli.py              # start, status, decide, resume, sweep, report
│   └── dashboard.py
└── tests/
    ├── conftest.py         # temp git repo fixture, FakeClock, fake LLM
    ├── test_tools.py
    ├── test_graph.py
    └── test_scenarios.py
```

---

## 10. EXECUTION PLAN
Stop after each phase (see rule 1).

- **Phase S – Claude Code setup:** apply Section 11 (plugins proposed then installed after approval, `.claude/settings.json`, `CLAUDE.md`, git hook, `.gitignore`). Commit it as the first commit.
- **Phase 0 – SK spike (short):** install `semantic-kernel`, check the version and the real Process Framework API, write a ~50-line throwaway prototype testing suspend + restart-at-step. Write `docs/phase0_sk_spike.md` with the findings and the chosen engine (A or B).
- **Phase 1 – Foundations:** `pyproject.toml`, `.env.example`, `config.py`, `clock.py`, `models/state.py`, `storage/sqlite_store.py`, `observability/telemetry.py`, with unit tests.
- **Phase 2 – LLM layer & tools:** `kernel_factory.py`, `llm/client.py`, `llm/fake.py`, all `tools/` modules, with unit tests (sandbox and git tested on a temp repo).
- **Phase 3 – Process:** `graph.py`, `steps.py`, `compensation.py`, `engine.py`, `test_graph.py`.
- **Phase 4 – CLI & scenarios:** `cli.py` and `test_scenarios.py` (all 9 tests green in mock mode), then one manual live run against OpenRouter to validate slugs and cost reporting.
- **Phase 5 – Dashboard:** `dashboard.py` (Streamlit): runs list, process graph from `graph.py` with the current step highlighted, diff, verification output, event journal, approval history, approve/revise/reject buttons with an actor name, cost and timing metrics.

Start with **Phase S** now.

---

## 11. CLAUDE CODE BEST PRACTICES

### 11.1 Team (fill in before starting)
```text
TEAM_MEMBER_1 = "Nassim Jazouli <nassim.jazouli@epfedu.fr>"
TEAM_MEMBER_2 = "Minh Nguyen <minh804.nguyen@gmail.com>"
```
Use the email attached to each member's GitHub account, otherwise GitHub does not credit the co-author.

### 11.2 Commit attribution: team members only, never Claude
1. Create `.claude/settings.json` (committed, shared by the team):
```json
{
  "attribution": { "commit": "", "pr": "", "sessionUrl": false },
  "permissions": {
    "deny": ["Read(./.env)", "Read(./.env.*)", "Bash(git push --force:*)"]
  }
}
```
   `attribution` with empty strings removes Claude's `Co-Authored-By` trailer and PR footer (`includeCoAuthoredBy` is deprecated). If the installed Claude Code version rejects a key, check its settings docs and adapt.
2. Every commit message ends with a `Co-authored-by:` trailer for each team member **who is not the commit author**:
```text
feat(process): add transition table and graph tests

Co-authored-by: Prénom Nom <email@example.com>
```
3. Enforce it with a git hook in `.githooks/commit-msg`, activated by each member with `git config core.hooksPath .githooks`:
```sh
#!/bin/sh
msg="$1"
if grep -qiE '^(Co-authored-by:.*(claude|anthropic)|Claude-Session:)' "$msg"; then
  echo "commit-msg: remove Claude attribution, use team Co-authored-by trailers" >&2; exit 1
fi
if ! grep -qiE '^Co-authored-by: ' "$msg"; then
  echo "commit-msg: add a Co-authored-by trailer for your teammate(s)" >&2; exit 1
fi
```
   The hook also covers commits made by plugins (e.g. `commit-commands`). Never bypass it with `--no-verify`.

### 11.3 Plugins & skills (Phase S)
Goal: install the best-suited plugins for this project, from trusted sources, at **project scope** so both team members share them.

1. Start from this shortlist (all in Anthropic's official marketplace `claude-plugins-official`, registered by default):
   | Plugin | Why for this project |
   |---|---|
   | `superpowers` | TDD, systematic debugging, verification-before-completion skills (rule 2). Our Section 10 plan **overrides** its own plan-writing workflow. |
   | `pyright-lsp` | Python type checking and navigation (strict typing). Needs `npm install -g pyright` on the PATH. |
   | `context7` (or the Context7 MCP server if not listed) | Up-to-date docs for Semantic Kernel, OpenTelemetry, Pydantic (rule 4). |
   | `security-guidance` | The project executes LLM-generated code and handles API keys. |
   | `code-review` | Review of each phase before merging. |
   | `commit-commands` | Commit / PR commands (subject to the hook in 11.2). |
2. Check each one actually exists (`claude plugin marketplace list`, the `/plugin` Discover tab, or claude.com/marketplace) and look at its context cost. You may propose **at most two** additional plugins if clearly better suited, with a one-line justification each.
3. Present the final list to the user and **wait for approval**. Never add a third-party marketplace without explicit approval.
4. Install from the shell: `claude plugin install <name>@claude-plugins-official --scope project`, then ask the user to run `/reload-plugins` (or restart). Commit `.claude/settings.json`; the teammate runs the same install commands once on their machine (list them in the README).

### 11.4 Project memory
- Copy this specification into `docs/SPEC.md`.
- Create a short `CLAUDE.md` (≤ 60 lines) at the repo root: project purpose, commands (`pytest`, `ruff check`, `python -m src.cli …`), architecture in 5 lines, the non-negotiable rules (explicit graph, structured LLM outputs, mock-first, injected clock, no secrets in logs, commit trailers), and "read `docs/SPEC.md` before each phase".
- Update `CLAUDE.md` at the end of each phase if a convention changed.

### 11.5 Working rules
- **One branch per phase** (`phase-1-foundations`, …), merged by a pull request reviewed by the other team member (use `code-review` first). Never commit directly to `main`.
- **Small commits** in Conventional Commits format (`feat`, `fix`, `test`, `refactor`, `docs`, `chore`), each with tests passing.
- **Plan before code** at the start of each phase (plan mode), then implement; run `/clear` between phases and rely on `CLAUDE.md` + `docs/SPEC.md` rather than a long conversation.
- **Before saying a phase is done:** `pytest` green, `ruff check` clean, `pyright` with no errors on `src/`, then the phase summary.
- **Secrets:** `.env` in `.gitignore`, only `.env.example` committed; never print the API key in logs, traces or test output.
- **Live calls:** only in Phase 4 and only after asking the user; report the cost of each live run.
- **No destructive git** (`push --force`, `reset --hard` on shared branches) without explicit approval.

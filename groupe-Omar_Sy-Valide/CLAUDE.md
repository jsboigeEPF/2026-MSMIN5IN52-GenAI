# Mini-OpenCode — project memory

Deterministic, stateful, observable coding agent with human-in-the-loop approval
(course catalogue B1). Python 3.11+, Microsoft Semantic Kernel, OpenRouter, SQLite, OpenTelemetry.

**Read `docs/SPEC.md` before each phase.** It is the source of truth; this file is a summary.

## Scope (course rules, override the spec)
- Edit only `groupe-Omar_Sy-Valide/` (see `../AGENTS.md`). Never touch `../docs/`, `../.github/`, other groups.
- Claude Code is launched from the repo root; `../.claude/settings.json` is local (gitignored by the course
  repo) and mirrors the committed copy `.claude/settings.json` here. Keep both identical.
- All commands below run from `groupe-Omar_Sy-Valide/` with `.venv` active (Windows: `.venv\Scripts\activate`).
- Time comes from `src/clock.py` (`SystemClock` / `FakeClock`); settings from `src/config.py` (`Settings`).

## Commands
```sh
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # once
pytest                         # all tests, mock LLM only
ruff check src tests
pyright src
python omar.py <repo>          # the interactive agent (Textual TUI, src/tui/), live models only
python -m src.cli --help       # start | status | decide | resume | undo | sweep | report  (--mock-llm)
streamlit run src/dashboard.py # Phase 5
docker build -t mini-opencode-sandbox sandbox/   # optional, only for SANDBOX_MODE=docker
```

## Architecture (5 lines)
1. `process/graph.py`: `TRANSITIONS` `(step, event, guard) -> step | Terminal`, the only routing logic.
2. `process/engine.py`: in-house `ProcessRunner` (start/status/resume/decide/sweep_expired; SK spike: decision B).
   Suspends on both gates (`graph.GATES`). Persisted `current_step` = next step to run. `LLMError`/`SandboxUnavailableError` -> FAILED + compensation;
   any other exception is a crash and stays resumable. `loop_iteration` keys checkpoints and the LLM cache.
3. `process/steps.py`: plan → plan_approval → code → write_approval → verify → finalize (writes the user's file,
   no commit; `undo` restores it); `compensation.py` discards the worktree. No code runs before the write gate.
4. `llm/`: structured-output calls via SK services `planner` / `coder` / `summarizer`, LLM cache, cost; `fake.py` for mock.
5. `tools/`: deterministic git worktree, AST inspector, patch (diff/apply), checks (tests/lint per language,
   `.omar.toml`), sandbox (subprocess default | docker), git helpers; `storage/` SQLite journal. `.env` and the default DB are anchored to the project dir.

## Non-negotiable rules
- Graph transitions are code, never decided by an LLM. Tools are called by step code, never by the LLM.
- LLM outputs are Pydantic-validated structured outputs (one retry, then FAILED).
- Mock-first: every step runs and is tested with the fake LLM before any live OpenRouter call.
- Live OpenRouter calls only in Phase 4, only after asking the user; report the cost of each run.
- Inject `Clock` everywhere; never call `datetime.now()` directly. Tests use `FakeClock`, no `sleep`.
- Model slugs only in `.env`; never hardcode model names in code or tests.
- No secrets in commits, logs, traces or test output. Only `.env.example` (no values) is committed.
- No placeholder code (`# TODO`, bare `pass`, `NotImplementedError`) on the nominal path.
- Verify `semantic_kernel` imports against the installed version (Context7 or package inspection).
  SK 1.45 gotchas: a function-invocation filter's 2nd parameter must be named `next`; native-function
  params must be typed `Any` to receive custom objects; errors come wrapped in `KernelInvokeException`.
- `openai` 3.x uses `httpx2` (not `httpx`); inject `httpx2.MockTransport` to test live paths offline.

## Git conventions
- One branch per phase (`phase-1-foundations`, …), PR reviewed by the teammate. Never commit to `main`.
- Conventional Commits, small, tests passing.
- Every commit ends with `Co-authored-by:` for each team member who is not the author:
  - `Nassim Jazouli <nassim.jazouli@epfedu.fr>`
  - `Minh Nguyen <minh804.nguyen@gmail.com>`
- No Claude trailer in commits (hook `.githooks/commit-msg`); the AI contribution is declared in `README.md`.
- Never `--no-verify`, `push --force`, or `reset --hard` on shared branches without explicit approval.

## Phase gate
End of each phase: `pytest` green, `ruff check` clean, `pyright src` 0 errors, short summary, then STOP
and wait for "continue". Update this file if a convention changed.

# Mini-OpenCode — project memory

Deterministic, stateful, observable coding agent with human-in-the-loop approval
(course catalogue B1). Python 3.11+, Microsoft Semantic Kernel, OpenRouter, SQLite, OpenTelemetry.

**Read `docs/SPEC.md` before each phase.** It is the source of truth; this file is a summary.

## Scope (course rules, override the spec)
- Edit only `groupe-Omar_Sy-Valide/` (see `../AGENTS.md`). Never touch `../docs/`, `../.github/`, other groups.
- Claude Code is launched from the repo root; `../.claude/settings.json` is local (gitignored by the course
  repo) and mirrors the committed copy `.claude/settings.json` here. Keep both identical.
- All commands below run from `groupe-Omar_Sy-Valide/`.

## Commands
```sh
pytest                         # all tests, mock LLM only
ruff check src tests
pyright src
python -m src.cli --help       # start | status | decide | resume | sweep | report  (--mock-llm)
streamlit run src/dashboard.py # Phase 5
```

## Architecture (5 lines)
1. `process/graph.py`: transition table `(step, event) -> step`, the single source of truth for routing.
2. `process/engine.py`: `ProcessRunner` (start / resume / decide / sweep_expired), persists state after each step.
3. `process/steps.py`: plan → code → verify → approval → finalize; `compensation.py` discards or reverts.
4. `llm/`: structured-output calls via SK services `planner` / `coder` / `summarizer`, LLM cache, cost; `fake.py` for mock.
5. `tools/`: deterministic git worktree, AST inspector, patch, sandbox (docker|subprocess), git ops; `storage/` SQLite journal.

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

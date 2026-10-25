# Phase 4 — live run against OpenRouter (2026-10-25)

One real run on a throwaway demo repository, to validate model slugs, structured outputs, cost reporting
and the human-approval loop outside mock mode. Run id `live-1`, scenario `live-demo`.

## Setup
- Demo repo: `calc.py` with two bugs (`add()` subtracts; `mean([])` divides by zero instead of raising
  `ValueError`) and `tests/test_calc.py` (3 tests, 2 failing).
- Task: *"Fix calc.py so all tests pass: add() must return the sum, and mean() must raise ValueError on an
  empty list."*
- Sandbox: Docker (`mini-opencode-sandbox`, `--network none`, worktree mounted read-only).
- Key: course key, restricted by the workspace guardrail to 8 models (`/api/v1/models/user`).

| Role | Model (from `.env`) | Price $/M tokens (in / out) |
|---|---|---|
| planner | `qwen/qwen3.8-flash` | 0.15 / 0.47 |
| coder | `deepseek/deepseek-v4.1-flash` | 0.02 / 1.00 |
| summarizer | `z-ai/glm-5.3-flash` | 0.05 / 0.07 |

## Timeline (from the event journal)
1. `start` with `deepseek/deepseek-v4-flash` as planner: OpenRouter answered **HTTP 404, model blocked by
   guardrail**. The run stayed at `plan` (resumable); nothing was charged.
2. `.env` models changed to the allowed set above, then `resume live-1`: the run restarted at `plan`.
3. First patch fixed both bugs (pytest passed) but ruff failed with **EXE002** "file is executable but no
   shebang". The summarizer condensed this, the auto-fix loop (fix_iteration 1) produced a second patch
   that passed — by adding a `#!/usr/bin/env python3` line.
4. Human decision `revise`: *"Remove the shebang line, it is not needed"*. The comment reached the planner
   (plan step 1), the new diff contained only the two fixes, verification passed.
5. Human decision `approve`: commit on the run branch, fast-forward of `main`, post-commit pytest on
   `main` passed, worktree removed → **COMPLETED**.

## Results (`python -m src.cli report`)
| Metric | Value |
|---|---|
| Completion | 1 / 1 |
| Fix iterations / revision iterations | 1 / 1 |
| Compute time, mean (P95) | plan 8.9 s (11.7 s), code 2.0 s (3.0 s), verify 1.3 s (1.9 s), finalize 1.3 s |
| Human wait (2 decisions), mean | 103 s (reported separately from compute) |
| Tokens | 2,111 prompt / 1,287 completion |
| **Cost** | **$0.000829 = 0.00076 EUR (0.008 % of the 10 EUR budget)** |

## Defects found by the live run (fixed in this phase)
- **Sandbox false positive.** Docker Desktop bind mounts show every file as `rwxrwxrwx`, and ruff 0.17
  enables the EXE rules by default, so every file failed EXE002 and the coder "fixed" it with a shebang.
  EXE001/EXE002 (executable-bit checks, not part of what the agent generates or commits) are now ignored;
  a Docker test reproduces the case.
- **Provider errors.** A blocked model surfaced as a 30 KB traceback. Provider errors now raise
  `LLMProviderError` (one line: service, model, HTTP status, reason); the run stays resumable and the CLI
  prints the `resume` command. The persisted status now also reflects the running step.
- **Routing to compatible providers.** Requests set `provider.require_parameters` so OpenRouter only
  routes to providers that support the JSON-schema response format.

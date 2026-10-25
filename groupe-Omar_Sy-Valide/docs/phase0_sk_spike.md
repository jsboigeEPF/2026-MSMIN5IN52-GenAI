# Phase 0 — Semantic Kernel Process Framework spike

**Decision: Option B (in-house engine).** Semantic Kernel is still used for the LLM services
(`planner`, `coder`, `summarizer` over OpenRouter) and for function-invocation filters (tracing).

## Environment
- Python 3.13.3 (Windows), `semantic-kernel==1.45.0` (to be pinned in `pyproject.toml`, Phase 1).
- API checked by reading the installed package (`semantic_kernel/processes/`), not from memory.
  Every public Process Framework entry point is decorated `@experimental`
  (`ProcessBuilder`, `KernelProcessStep`, `local_kernel_process.start`, `KernelProcessStateMetadata`).

## Real API used
| Need | Installed API |
|---|---|
| Build graph | `ProcessBuilder(name)`, `add_step(StepCls, initial_state=...)`, `on_input_event(id).send_event_to(step, function_name=, parameter_name=)`, `step.on_event(id).send_event_to(...)` |
| Step | `KernelProcessStep[TState]` with `activate(KernelProcessStepState[TState])`, `@kernel_function` methods, `KernelProcessStepContext.emit_event(id)` |
| Run | `semantic_kernel.processes.local_runtime.local_kernel_process.start(process, kernel, initial_event)` → `LocalKernelProcessContext` (runs until the message queue drains, `keep_alive=False`) |
| Snapshot | `await ctx.get_state()` → `process_state_metadata_utils.kernel_process_to_process_state_metadata(...)` → `KernelProcessStateMetadata` (Pydantic, JSON round-trip with `by_alias=True`) |
| Restore | `ProcessBuilder.build(state_metadata=KernelProcessStateMetadata.model_validate_json(...))` |

## Prototype
[`spikes/phase0_sk_spike.py`](../spikes/phase0_sk_spike.py) (~120 lines, throwaway, not imported by `src/`).
Graph `Plan → Code → Approval → Finalize`; a `Counter` stands in for the fake-LLM call counter.
Run: `.venv/Scripts/python -W ignore spikes/phase0_sk_spike.py`.

Output on SK 1.45.0:
```
after start:          {'Plan': 1, 'Code': 1}                    # Approval.request emits nothing → run returns
after approve:        {'Plan': 1, 'Code': 1, 'Finalize': 1}     # fresh process from JSON + HumanDecision event
after resume-at-code: {'Plan': 1, 'Code': 2, 'Finalize': 1}     # fresh process from JSON + ResumeAtCode event
```
The persisted JSON contains each step's own state (`{"Approval": {"state": {"awaiting": true}}}`, …) and the
typed `TState` is restored (the `decide` guard on `awaiting` passed).

## Findings
1. **Suspend at approval — works.** A step that emits no event lets the local runtime drain and return.
   "Suspended" is not a runtime concept, only a flag we keep in step state.
2. **Restart at a given step — works, but only through our own bookkeeping.**
   - The snapshot holds per-step state only: no pending message queue, no cursor, no "step in progress".
   - Restarting at a step needs one explicit external input edge per re-entry point
     (`ResumeAtCode`, `HumanDecision`, …), and *we* must decide which one to fire.
   - The process `id` is regenerated on each `build()`; the `run_id` must be ours.
3. **No per-step checkpoint hook.** `get_state()` is only usable after the whole run drains. The spec's
   `step_started` / `step_completed` events and "persist after each step" (§5.3) would have to be written
   by every step itself, outside the framework.
4. **Step exceptions are swallowed.** `LocalStep.handle_message` catches the exception, logs it and emits
   `<function>.OnError` (`local_runtime/local_step.py:165-167`). With no edge on that event, `start()` returns
   normally: a crash looks like a clean finish unless every step gets an `OnError` edge.
5. **Routing guards** (`fix_iteration < MAX_FIX`, `revision_iteration < MAX_REVISIONS`) cannot live on edges;
   they would sit inside step code choosing which event to emit, so the routing would be split between
   step code and SK edges instead of one transition table (§5.1).
6. **Stability.** The whole Process Framework is `@experimental` and may change between minor versions.

## Why Option B
The two hard criteria of §5.2 are technically met, but everything §5.3 requires — journaled
`step_started`/`step_completed`, persist after each step, resume at the first incomplete step, crash
visibility — would be implemented by us around the framework, and the transition table would be
duplicated as SK edges plus re-entry edges. An in-house async loop over `graph.py` gives the same
behaviour with one source of truth, explicit errors and testable resumption, in less code.

Option B keeps the `ProcessRunner` interface from §5.2 (`start`, `resume`, `decide`, `sweep_expired`).

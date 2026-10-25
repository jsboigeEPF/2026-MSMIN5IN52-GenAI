"""Phase 0 throwaway spike: can the SK Process Framework suspend at approval and restart at a given step?"""
import asyncio
from collections import Counter

from pydantic import BaseModel
from semantic_kernel import Kernel
from semantic_kernel.functions import kernel_function
from semantic_kernel.processes import ProcessBuilder
from semantic_kernel.processes.kernel_process.kernel_process_event import (
    KernelProcessEvent,
)
from semantic_kernel.processes.kernel_process.kernel_process_step import (
    KernelProcessStep,
)
from semantic_kernel.processes.kernel_process.kernel_process_step_context import (
    KernelProcessStepContext,
)
from semantic_kernel.processes.kernel_process.kernel_process_step_state import (
    KernelProcessStepState,
)
from semantic_kernel.processes.kernel_process.kernel_process_step_state_metadata import (
    KernelProcessStateMetadata,
)
from semantic_kernel.processes.local_runtime.local_kernel_process import start
from semantic_kernel.processes.process_state_metadata_utils import (
    kernel_process_to_process_state_metadata,
)

CALLS: Counter[str] = Counter()  # stands in for the fake-LLM call counter


class S(BaseModel):
    runs: int = 0
    awaiting: bool = False


class Base(KernelProcessStep[S]):
    state: S | None = None
    event: str = ""

    async def activate(self, state: KernelProcessStepState[S]) -> None:
        self.state = state.state

    @kernel_function
    async def run(self, context: KernelProcessStepContext) -> None:
        CALLS[type(self).__name__] += 1
        assert self.state is not None
        self.state.runs += 1
        await context.emit_event(self.event)


class Plan(Base):
    event: str = "PlanCreated"


class Code(Base):
    event: str = "CodeGenerated"


class Finalize(Base):
    event: str = "Committed"


class Approval(KernelProcessStep[S]):
    state: S | None = None

    async def activate(self, state: KernelProcessStepState[S]) -> None:
        self.state = state.state

    @kernel_function
    async def request(self) -> None:  # suspends: emits nothing, so the local runtime drains and returns
        assert self.state is not None
        self.state.awaiting = True

    @kernel_function
    async def decide(self, context: KernelProcessStepContext, decision: str) -> None:
        assert self.state is not None and self.state.awaiting, "decision without pending approval"
        self.state.awaiting = False
        if decision == "approve":
            await context.emit_event("Approved")


def builder() -> ProcessBuilder:
    pb = ProcessBuilder(name="spike")
    plan, code = pb.add_step(Plan, initial_state=S()), pb.add_step(Code, initial_state=S())
    appr, fin = pb.add_step(Approval, initial_state=S()), pb.add_step(Finalize, initial_state=S())
    pb.on_input_event("Start").send_event_to(plan, function_name="run")
    plan.on_event("PlanCreated").send_event_to(code, function_name="run")
    code.on_event("CodeGenerated").send_event_to(appr, function_name="request")
    appr.on_event("Approved").send_event_to(fin, function_name="run")
    pb.on_input_event("HumanDecision").send_event_to(appr, function_name="decide", parameter_name="decision")
    pb.on_input_event("ResumeAtCode").send_event_to(code, function_name="run")  # explicit re-entry edge
    return pb


async def run(meta: KernelProcessStateMetadata | None, event: KernelProcessEvent) -> str:
    ctx = await start(builder().build(state_metadata=meta), Kernel(), event)
    return kernel_process_to_process_state_metadata(await ctx.get_state()).model_dump_json(by_alias=True)


def load(js: str) -> KernelProcessStateMetadata:
    return KernelProcessStateMetadata.model_validate_json(js)


async def main() -> None:
    # 1. start -> plan -> code -> approval (suspend)
    saved = await run(None, KernelProcessEvent(id="Start"))
    print("after start:", dict(CALLS), "\n  persisted:", saved)
    # 2. fresh process from persisted JSON, deliver the human decision
    done = await run(load(saved), KernelProcessEvent(id="HumanDecision", data="approve"))
    print("after approve:", dict(CALLS), "\n  persisted:", done)
    # 3. crash-recovery: restart at Code from the step-1 snapshot
    await run(load(saved), KernelProcessEvent(id="ResumeAtCode"))
    print("after resume-at-code:", dict(CALLS))


asyncio.run(main())

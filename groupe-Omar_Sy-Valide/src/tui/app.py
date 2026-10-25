"""Omar Sy Valide: the interactive, full-screen front end of the agent (Textual).

Type a task; the planner picks the file and proposes a plan; you answer Oui / Non / Affiner. Then the coder
proposes a diff; same three answers. Only then is the file written (in a worktree first, where the tests run)
and, once the tests pass, copied into your working tree without a commit.

The screen drives the same `ProcessRunner` as the CLI and the dashboard: every transition still comes from
`graph.TRANSITIONS`, every step is persisted, every decision is journaled with your login as the actor.
"""

import argparse
import getpass
import io
import logging
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, ClassVar, Literal

from rich.console import Console
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Static

from src.config import DEFAULT_DB, PROJECT_DIR, Settings
from src.llm.client import LLMProviderError
from src.llm.fake import FakeScriptExhaustedError
from src.models.state import TERMINAL_STATUSES, AgentProcessState, ProcessStatus
from src.process.engine import DecisionRefusedError, DecisionRequest, ProcessRunner, StartRequest
from src.process.graph import GATES, Step
from src.process.steps import HumanApprovalStep
from src.runtime import open_runner
from src.safe_text import printable
from src.tools.git_ops import GitError, run_git
from src.tools.sandbox import SandboxUnavailableError
from src.tui import render

Action = Literal["approve", "revise", "reject"]

# Errors the user can act on: shown in one line, the session goes on.
EXPECTED_ERRORS = (
    DecisionRefusedError,
    LLMProviderError,
    SandboxUnavailableError,
    FakeScriptExhaustedError,
    GitError,
    LookupError,
    ValueError,
)

HELP = """\
Écris une tâche (ex. « corrige add() et mean() pour que les tests passent ») puis Entrée.
À chaque validation : o = Oui · n = Non · a = Affiner (tu écris ce qu'il faut changer).

/runs            liste des runs          /resume <id>   reprendre un run
/undo [id]       annuler la dernière écriture
/repo <chemin>   changer de dépôt cible  /clear         vider l'écran
/help            cette aide              /quit          quitter (ou Ctrl+Q)"""


def find_repo(path: Path) -> Path:
    """Top level of the git repository containing `path`; refuses the agent's own repository."""
    top = Path(run_git(path.expanduser().resolve(), "rev-parse", "--show-toplevel"))
    if PROJECT_DIR.is_relative_to(top):
        raise ValueError(
            f"{top} contient l'agent lui-même : donne le dépôt à modifier, par ex. /repo ../../demo-calc"
        )
    return top


def _login() -> str:
    try:
        return getpass.getuser() or "local-user"
    except (OSError, KeyError):  # no USER/LOGNAME and no passwd entry (minimal containers)
        return "local-user"


class OmarApp(App[None]):
    TITLE = "Omar Sy Valide"
    CSS = """
    #main { height: 1fr; }
    #chat { width: 1fr; padding: 0 1; }
    #chat > Static { width: 100%; height: auto; }
    #sidebar { width: 34; height: 100%; padding: 1 1; border-left: solid $primary-background; }
    #gate { height: auto; padding: 0 1; display: none; background: $boost; }
    #gate.visible { display: block; }
    #gate-question { width: 1fr; padding: 1 0; text-style: bold; }
    #gate Button { margin: 0 1; min-width: 14; }
    #prompt { margin: 0 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("o", "decide('approve')", "Oui"),
        Binding("n", "decide('reject')", "Non"),
        Binding("a", "refine", "Affiner"),
        Binding("escape", "cancel_refine", "Annuler", show=False),
        Binding("ctrl+q", "quit", "Quitter", priority=True),
    ]

    def __init__(
        self,
        repo: Path | None = None,
        *,
        db: Path = DEFAULT_DB,
        llm_script: Path | None = None,  # tests only: scripted fake LLM instead of OpenRouter
    ) -> None:
        super().__init__()
        self.repo_arg = repo
        self.db = db
        self.llm_script = llm_script
        self.transcript: list[str] = []  # plain text of everything shown, for tests and debugging
        self.actor = _login()
        self.repo: Path | None = None
        self.runner: ProcessRunner | None = None
        self.state: AgentProcessState | None = None
        self.run_id: str | None = None  # the run on screen; engine events of other runs are ignored
        self.visited: set[str] = set()
        self.cost_eur = 0.0
        self.busy = False
        self.refining = False
        self._stack = AsyncExitStack()

    # --- layout ------------------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main"):
            yield VerticalScroll(id="chat")
            yield Static(id="sidebar")
        with Horizontal(id="gate"):
            yield Static(id="gate-question")
            yield Button("Oui (o)", id="approve", variant="success")
            yield Button("Non (n)", id="reject", variant="error")
            yield Button("Affiner (a)", id="revise", variant="warning")
        yield Input(id="prompt", placeholder="Décris la tâche… (/help pour l'aide)")
        yield Footer()

    async def on_mount(self) -> None:
        self.say(Text("Omar Sy Valide — agent de code avec validation humaine.", style="bold magenta"))
        self.say(Text(HELP, style="dim"))
        try:
            self.runner = await self._stack.enter_async_context(
                open_runner(self.db, drive=True, mock=self.llm_script is not None, script=self.llm_script)
            )
        except EXPECTED_ERRORS as exc:
            self.error(f"{exc}\nRenseigne la clé et les modèles dans .env (voir .env.example).")
            return
        self.runner.on_event = self._on_engine_event
        self.set_repo(self.repo_arg or Path.cwd())
        self.refresh_sidebar()
        self.query_one("#prompt", Input).focus()

    async def on_unmount(self) -> None:
        await self._stack.aclose()

    # --- output helpers ------------------------------------------------------------------------------------

    def say(self, renderable: Any) -> None:
        """One block per message: a Static re-renders at the new width when the terminal is resized."""
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(Static(renderable))
        chat.scroll_end(animate=False)
        plain = Console(file=io.StringIO(), width=160, color_system=None, record=True)
        plain.print(renderable)
        self.transcript.append(plain.export_text())

    def error(self, message: str) -> None:
        self.say(Text(f"✗ {printable(message)}", style="red"))

    def refresh_sidebar(self) -> None:
        self.query_one("#sidebar", Static).update(render.sidebar(self.state, self.visited, self.cost_eur))

    def set_repo(self, path: Path) -> None:
        try:
            self.repo = find_repo(path)
        except (GitError, ValueError) as exc:
            self.repo = None
            self.error(f"pas de dépôt cible : {exc}" if isinstance(exc, GitError) else str(exc))
            self.say(Text("Indique le dépôt à modifier avec /repo <chemin>.", style="yellow"))
            self.sub_title = "aucun dépôt"
            return
        branch = run_git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
        self.sub_title = f"{self.repo.name} · {branch}"
        self.say(Text.assemble("Dépôt cible : ", (str(self.repo), "bold"), f" (branche {branch})"))

    # --- engine events (same event loop: widgets can be updated directly) ----------------------------------

    def _on_engine_event(
        self, state: AgentProcessState, step: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        if state.run_id != self.run_id:
            return  # e.g. the expiry sweep closing an older run
        self.state = state
        if event_type == "step_completed":
            self.visited.add(step)
        if event_type == "step_started" and step in set(Step) and Step(step) not in GATES:
            self.say(render.progress_line(Step(step)))
        if event_type == "step_completed" and step == Step.VERIFY and state.verification is not None:
            self.say(render.verification_text(state.verification))
        self.refresh_sidebar()

    # --- gate bar ------------------------------------------------------------------------------------------

    def pending_gate(self) -> str | None:
        return HumanApprovalStep.gate(self.state) if self.state is not None else None

    def show_gate(self, gate: str | None) -> None:
        bar = self.query_one("#gate")
        bar.set_class(gate is not None, "visible")
        if gate is not None:
            self.query_one("#gate-question", Static).update(render.GATE_QUESTIONS[gate])
            self.query_one("#approve", Button).focus()
        else:
            self.query_one("#prompt", Input).focus()
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("decide", "refine"):
            if self.pending_gate() is None or self.busy:
                return False
            return not self.query_one("#prompt", Input).has_focus  # letters go to the input when typing
        if action == "cancel_refine":
            return self.refining
        return True

    async def show_state(self, state: AgentProcessState) -> None:
        self.state = state
        if self.runner is not None:
            usd = await self.runner.store.llm_cost_usd(state.run_id)
            self.cost_eur = self.runner.ctx.settings.usd_to_eur(usd)
        gate = self.pending_gate()
        if gate == "plan":
            self.say(render.plan_panel(state))
        elif gate == "write":
            self.say(render.diff_panel(state))
        elif state.status in TERMINAL_STATUSES:
            self.say(render.outcome_panel(state, self.cost_eur))
        self.show_gate(gate)
        self.refresh_sidebar()

    # --- user input --------------------------------------------------------------------------------------

    @on(Input.Submitted, "#prompt")
    async def submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            if self.refining:
                self.action_cancel_refine()
            await self.command(text)
        elif self.refining:
            self.end_refine()
            self.decide("revise", text)
            await self.command(text)
        elif self.busy:
            self.error("un traitement est en cours, patiente un instant")
        elif self.pending_gate() is not None:
            self.error("réponds d'abord à la validation en cours : o (oui), n (non) ou a (affiner)")
            self.show_gate(self.pending_gate())
        else:
            self.start_task(text)

    @on(Button.Pressed, "#gate Button")
    def gate_button(self, event: Button.Pressed) -> None:
        if event.button.id == "revise":
            self.action_refine()
        elif event.button.id in ("approve", "reject"):
            self.action_decide(event.button.id)

    def action_decide(self, action: str) -> None:
        if self.pending_gate() is None or self.busy:
            return
        self.decide("approve" if action == "approve" else "reject")

    def action_refine(self) -> None:
        if self.pending_gate() is None or self.busy:
            return
        self.refining = True
        prompt = self.query_one("#prompt", Input)
        prompt.placeholder = "Que faut-il changer ? (Entrée pour envoyer, Échap pour annuler)"
        prompt.focus()
        self.refresh_bindings()

    def action_cancel_refine(self) -> None:
        self.end_refine()
        self.show_gate(self.pending_gate())

    def end_refine(self) -> None:
        self.refining = False
        self.query_one("#prompt", Input).placeholder = "Décris la tâche… (/help pour l'aide)"
        self.refresh_bindings()

    # --- engine calls (async workers: the screen stays responsive) -------------------------------------

    def start_task(self, text: str) -> None:
        if self.runner is None or self.repo is None:
            self.error("aucun dépôt cible : /repo <chemin>")
            return
        self.say(render.user_message(text))
        self.visited = set()
        self.run_id = uuid.uuid4().hex[:12]
        request = StartRequest(
            target_repo_path=str(self.repo), user_prompt=text, scenario="tui", run_id=self.run_id
        )
        runner = self.runner

        async def go() -> AgentProcessState:
            return await runner.status(await runner.start(request))

        self.run_engine(go)

    def decide(self, action: Action, comment: str | None = None) -> None:
        if self.runner is None or self.state is None:
            return
        labels = {"approve": "Oui", "reject": "Non", "revise": f"Affiner : {comment}"}
        self.say(Text(f"> {printable(labels[action])}", style="bold cyan"))
        request = DecisionRequest(action=action, actor=self.actor, comment=comment)
        runner, run_id = self.runner, self.state.run_id
        self.run_engine(lambda: runner.decide(run_id, request))

    def run_engine(self, call: Any) -> None:
        self.busy = True
        self.show_gate(None)

        async def work() -> None:
            try:
                await self.show_state(await call())
            except EXPECTED_ERRORS as exc:
                self.error(str(exc))
                await self.reload()
            except Exception as exc:  # keep the session alive; the run is saved at its last step
                run = f" — reprends avec /resume {self.state.run_id}" if self.state else ""
                self.error(f"erreur inattendue : {type(exc).__name__}: {exc}{run}")
            finally:
                self.busy = False
                self.refresh_bindings()

        self.run_worker(work(), exclusive=True, group="engine")

    async def reload(self) -> None:
        """Re-read the run on screen (it may have expired or failed meanwhile)."""
        if self.runner is not None and self.run_id is not None:
            try:
                self.state = await self.runner.status(self.run_id)
            except LookupError:
                self.state = None
        if self.state is not None and self.state.status in TERMINAL_STATUSES:
            self.say(render.outcome_panel(self.state, self.cost_eur))
        self.show_gate(self.pending_gate())
        self.refresh_sidebar()

    # --- slash commands ------------------------------------------------------------------------------------

    async def command(self, text: str) -> None:
        name, _, arg = text.partition(" ")
        arg = arg.strip()
        if name == "/help":
            self.say(Text(HELP, style="dim"))
        elif name == "/quit":
            self.exit()
        elif name == "/clear":
            self.query_one("#chat", VerticalScroll).remove_children()
        elif name == "/repo":
            self.set_repo(Path(arg) if arg else Path.cwd())
        elif self.runner is None:
            self.error("le moteur n'a pas démarré (voir le message ci-dessus)")
        elif self.busy:
            self.error("un traitement est en cours, patiente un instant")
        elif name == "/runs":
            await self.list_runs()
        elif name == "/resume" and arg:
            await self.resume(arg)
        elif name == "/undo":
            await self.undo(arg or (self.state.run_id if self.state else ""))
        else:
            self.error(f"commande inconnue : {text} (/help)")

    async def list_runs(self) -> None:
        assert self.runner is not None
        await self.runner.sweep_expired()
        runs = (await self.runner.store.list_runs())[-10:]
        if not runs:
            self.say(Text("Aucun run pour l'instant.", style="dim"))
            return
        table = Table(title="Derniers runs", title_justify="left", expand=True)
        for column in ("run", "statut", "fichier", "tâche"):
            table.add_column(column)
        for r in runs:
            table.add_row(
                r.run_id,
                r.status.value,
                Text(printable(r.target_file or "-")),
                Text(printable(r.user_prompt[:50])),
            )
        self.say(table)

    async def resume(self, run_id: str) -> None:
        assert self.runner is not None
        try:
            state = await self.runner.status(run_id)
        except LookupError as exc:
            self.error(str(exc))
            return
        self.run_id = run_id
        events = await self.runner.store.list_events(run_id)
        self.visited = {e.step for e in events if e.event_type == "step_completed"}
        self.say(Text(f"Reprise du run {run_id} ({state.status.value})", style="bold"))
        if state.status in TERMINAL_STATUSES or state.status is ProcessStatus.AWAITING_APPROVAL:
            await self.show_state(state)
        else:
            runner = self.runner
            self.state = state
            self.run_engine(lambda: runner.resume(run_id))

    async def undo(self, run_id: str) -> None:
        assert self.runner is not None
        if not run_id:
            self.error("aucun run à annuler")
            return
        try:
            state = await self.runner.undo(run_id)
        except EXPECTED_ERRORS as exc:
            self.error(str(exc))
            return
        self.say(Text(f"↶ {state.target_file} restauré tel qu'avant le run {run_id}.", style="green"))


LOG_FILE = PROJECT_DIR / "omar.log"


def main(argv: list[str] | None = None) -> None:
    """`python omar.py [repo]`, or `omar` once the project is installed."""
    parser = argparse.ArgumentParser(prog="omar", description="Agent de code avec validation humaine.")
    parser.add_argument("repo", nargs="?", type=Path, help="dépôt git à modifier (défaut : dossier courant)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="base SQLite des runs")
    args = parser.parse_args(argv)
    Settings()  # fail fast on an invalid .env, before the screen takes over the terminal
    # Library logs (Semantic Kernel, httpx, warnings) go to a file: anything written to the terminal
    # behind Textual's back would corrupt the screen.
    logging.basicConfig(filename=LOG_FILE, level=logging.WARNING, format="%(asctime)s %(name)s: %(message)s")
    logging.captureWarnings(True)
    OmarApp(args.repo, db=args.db).run()

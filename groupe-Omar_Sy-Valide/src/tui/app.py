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
import time
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from rich.console import Console
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from src.config import DEFAULT_DB, PROJECT_DIR, Settings
from src.labels import status_label
from src.llm.client import LLMProviderError
from src.llm.fake import FakeScriptExhaustedError
from src.models.state import TERMINAL_STATUSES, AgentProcessState, ProcessStatus
from src.process.engine import DecisionRefusedError, DecisionRequest, ProcessRunner, StartRequest
from src.process.graph import GATES, Step
from src.process.steps import HumanApprovalStep
from src.runtime import open_runner
from src.safe_text import printable
from src.tools.git_ops import GitError, list_files, run_git
from src.tools.sandbox import SandboxUnavailableError
from src.tui import render
from src.tui.history import conversation
from src.tui.mentions import insert_mention, match_files, mention_at, mentioned_files

Action = Literal["approve", "revise", "reject", "write_anyway"]

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
@ pour citer un fichier du dépôt : il devient le fichier à modifier (sinon l'IA le choisit).
À chaque validation : o = Oui · n = Non · a = Affiner (tu écris ce qu'il faut changer).

/runs            liste des runs          /resume [id]   reprendre le dernier run (ou celui-ci)
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


PLACEHOLDER = "Décris la tâche… (@ pour citer un fichier, /help pour l'aide)"
_NEVER = datetime(1970, 1, 1, tzinfo=UTC)


class PromptInput(Input):
    """The task input. While the @ suggestions are open, ↑/↓ move in them and Entrée/Tab insert the file."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "mention_move(-1)", show=False),
        Binding("down", "mention_move(1)", show=False),
        Binding("tab", "mention_accept", show=False),
        Binding("escape", "mention_close", show=False),
    ]

    @property
    def omar(self) -> "OmarApp":
        app: object = self.app  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        assert isinstance(app, OmarApp)
        return app

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("mention_"):
            return self.omar.mentions_open  # otherwise the key keeps its usual meaning (Tab, Échap...)
        return super().check_action(action, parameters)

    def action_mention_move(self, step: int) -> None:
        self.omar.move_mention(step)

    def action_mention_accept(self) -> None:
        self.omar.accept_mention()

    def action_mention_close(self) -> None:
        self.omar.close_mentions()

    async def action_submit(self) -> None:
        if self.omar.mentions_open:
            self.omar.accept_mention()
            return
        await super().action_submit()


class LiveReply:
    """Shows an LLM reply while it is generated, in one block that the final result then replaces."""

    REFRESH_SEC = 0.08  # repaint at most ~12 times per second, however fast the tokens come

    def __init__(self, app: "OmarApp") -> None:
        self.app = app
        self.widget: Static | None = None
        self.service_id = ""
        self.text = ""
        self.reasoning = ""
        self._painted = 0.0

    def start(self, service_id: str) -> None:
        self.end()
        self.service_id, self.text, self.reasoning = service_id, "", ""
        self.widget = Static(render.live_panel(service_id, "", ""), classes="live")
        chat = self.app.query_one("#chat", VerticalScroll)
        chat.mount(self.widget)
        chat.scroll_end(animate=False)

    def token(self, text: str, reasoning: bool = False) -> None:
        if self.widget is None:
            self.start(self.service_id or "llm")
        if reasoning:
            self.reasoning += text
        else:
            self.text += text
        if time.monotonic() - self._painted >= self.REFRESH_SEC:
            self.paint()

    def paint(self) -> None:
        if self.widget is not None:
            self.widget.update(render.live_panel(self.service_id, self.text, self.reasoning))
            self.app.query_one("#chat", VerticalScroll).scroll_end(animate=False)
            self._painted = time.monotonic()

    def end(self) -> None:
        if self.widget is not None:
            self.widget.remove()
            self.widget = None


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
    #mentions { display: none; height: auto; max-height: 10; margin: 0 1; border: round $accent; }
    #mentions.visible { display: block; }
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
        self.live = LiveReply(self)
        self.files: list[str] = []  # the target repository's files, for @ mentions
        self.mention: tuple[int, str] | None = None  # the @mention being typed: (start, query)

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
        yield OptionList(id="mentions")
        yield PromptInput(id="prompt", placeholder=PLACEHOLDER)
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
        self.runner.ctx.stream = self.live
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
        self.files = list_files(self.repo)
        self.sub_title = f"{self.repo.name} · {branch}"
        self.say(Text.assemble("Dépôt cible : ", (str(self.repo), "bold"), f" (branche {branch})"))

    # --- engine events (same event loop: widgets can be updated directly) ----------------------------------

    def _on_engine_event(
        self, state: AgentProcessState, step: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        if state.run_id != self.run_id:
            return  # e.g. the expiry sweep closing an older run
        if event_type in ("step_completed", "step_failed"):
            self.live.end()  # the step's result replaces the live reply
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
        if gate is None:
            self.query_one("#prompt", Input).focus()
        else:
            anyway = self.write_anyway_pending()
            question = (
                render.gate_question(self.state, self.runner.limits) if self.state and self.runner else ""
            )
            self.query_one("#gate-question", Static).update(question)
            approve = self.query_one("#approve", Button)
            approve.label = "Écrire quand même (o)" if anyway else "Oui (o)"
            approve.variant = "warning" if anyway else "success"
            approve.focus()
        self.refresh_bindings()

    def write_anyway_pending(self) -> bool:
        if self.state is None or self.runner is None:
            return False
        return HumanApprovalStep.write_anyway_allowed(self.state, self.runner.limits)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("decide", "refine"):
            if self.pending_gate() is None or self.busy:
                return False
            return not self.query_one("#prompt", Input).has_focus  # letters go to the input when typing
        if action == "cancel_refine":
            return self.refining
        return True

    async def show_state(self, state: AgentProcessState, announce: bool = True) -> None:
        """Show where the run stands. `announce=False` when its history was just replayed (no duplicate)."""
        self.state = state
        if self.runner is not None:
            usd = await self.runner.store.llm_cost_usd(state.run_id)
            self.cost_eur = self.runner.ctx.settings.usd_to_eur(usd)
        gate = self.pending_gate()
        if announce and gate == "plan":
            self.say(render.plan_panel(state))
        elif announce and gate == "write":
            self.say(render.diff_panel(state))
        elif announce and state.status in TERMINAL_STATUSES:
            self.say(render.outcome_panel(state, self.cost_eur))
        self.show_gate(gate)
        self.refresh_sidebar()

    # --- @ mentions ---------------------------------------------------------------------------------------

    @property
    def mentions_open(self) -> bool:
        return self.query_one("#mentions", OptionList).has_class("visible")

    @on(Input.Changed, "#prompt")
    def prompt_changed(self, event: Input.Changed) -> None:
        if self.refining or self.repo is None:
            self.close_mentions()
            return
        prompt = self.query_one("#prompt", Input)
        self.mention = mention_at(event.value, prompt.cursor_position)
        if self.mention is None:
            self.close_mentions()
            return
        if self.mention[1] == "":  # a fresh "@": pick up files created since
            self.files = list_files(self.repo)
        suggestions = match_files(self.files, self.mention[1])
        menu = self.query_one("#mentions", OptionList)
        menu.clear_options()
        if not suggestions:
            self.close_mentions()
            return
        menu.add_options([Option(Text(printable(path)), id=path) for path in suggestions])
        menu.highlighted = 0
        menu.add_class("visible")

    def close_mentions(self) -> None:
        self.query_one("#mentions", OptionList).remove_class("visible")

    def move_mention(self, step: int) -> None:
        menu = self.query_one("#mentions", OptionList)
        if menu.option_count:
            menu.highlighted = ((menu.highlighted or 0) + step) % menu.option_count

    def accept_mention(self, index: int | None = None) -> None:
        menu = self.query_one("#mentions", OptionList)
        chosen = index if index is not None else menu.highlighted
        if self.mention is None or chosen is None or not menu.option_count:
            self.close_mentions()
            return
        path = menu.get_option_at_index(chosen).id or ""
        prompt = self.query_one("#prompt", Input)
        value, cursor = insert_mention(prompt.value, prompt.cursor_position, self.mention[0], path)
        self.close_mentions()
        prompt.value, prompt.cursor_position = value, cursor
        self.mention = None

    @on(OptionList.OptionSelected, "#mentions")
    def mention_clicked(self, event: OptionList.OptionSelected) -> None:
        self.accept_mention(event.option_index)
        self.query_one("#prompt", Input).focus()

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
        if action == "approve":
            self.decide("write_anyway" if self.write_anyway_pending() else "approve")
        else:
            self.decide("reject")

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
        self.query_one("#prompt", Input).placeholder = PLACEHOLDER
        self.refresh_bindings()

    # --- engine calls (async workers: the screen stays responsive) -------------------------------------

    def start_task(self, text: str) -> None:
        if self.runner is None or self.repo is None:
            self.error("aucun dépôt cible : /repo <chemin>")
            return
        self.say(render.user_message(text))
        mentioned = mentioned_files(text, self.files)
        target = mentioned[0] if mentioned else None
        if len(mentioned) > 1:
            self.say(Text(f"Un seul fichier par tâche : {target} sera modifié.", "yellow"))
        self.visited = set()
        self.run_id = uuid.uuid4().hex[:12]
        request = StartRequest(
            target_repo_path=str(self.repo),
            target_file=target,
            user_prompt=text,
            scenario="tui",
            run_id=self.run_id,
        )
        runner = self.runner

        async def go() -> AgentProcessState:
            return await runner.status(await runner.start(request))

        self.run_engine(go)

    def decide(self, action: Action, comment: str | None = None) -> None:
        if self.runner is None or self.state is None:
            return
        labels = {
            "approve": "Oui",
            "write_anyway": "Écrire quand même",
            "reject": "Non",
            "revise": f"Affiner : {comment}",
        }
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
                self.live.end()
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
        elif name == "/resume":
            run_id = arg or await self.latest_run_id()
            if run_id:
                await self.resume(run_id)
            else:
                self.error("aucun run à reprendre dans ce dépôt")
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

    async def latest_run_id(self) -> str | None:
        """The most recent run on the current repository."""
        assert self.runner is not None
        starts = await self.runner.store.run_starts()
        mine = [s.run_id for s in await self.runner.store.list_runs() if s.target_repo_path == str(self.repo)]
        return max(mine, key=lambda run_id: starts.get(run_id, _NEVER), default=None)

    async def resume(self, run_id: str) -> None:
        assert self.runner is not None
        try:
            state = await self.runner.status(run_id)
        except LookupError as exc:
            self.error(str(exc))
            return
        self.run_id = run_id
        store = self.runner.store
        events = await store.list_events(run_id)
        self.visited = {e.step for e in events if e.event_type == "step_completed"}
        self.cost_eur = self.runner.ctx.settings.usd_to_eur(await store.llm_cost_usd(run_id))
        self.say(Text(f"Reprise du run {run_id} ({status_label(state)}) : historique", style="bold"))
        for item in conversation(
            state,
            events,
            await store.list_approvals(run_id),
            await store.llm_responses(run_id),
            self.cost_eur,
        ):
            self.say(item)
        if state.status in TERMINAL_STATUSES or state.status is ProcessStatus.AWAITING_APPROVAL:
            await self.show_state(state, announce=False)
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

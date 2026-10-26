"""Interactive MCP client that uses a local Ollama model for tool selection."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

try:
    import readline  # noqa: F401  (arrow keys and history for input(); absent on Windows)
except ImportError:
    pass

PROJECT_DIR = Path(__file__).resolve().parent
MUTATING_TOOLS = {"write_file", "create_directory", "move_file"}
MAX_TOOL_ROUNDS = 8
SYSTEM_PROMPT = """Tu es un assistant francophone qui consulte et modifie des fichiers grâce à des outils MCP.
Tu n'as accès qu'au dossier sandbox/, et tous les chemins sont relatifs à ce dossier :
écris "data/fichier.json", jamais "sandbox/data/fichier.json", "/" ni un chemin absolu.
Pour lister tout le sandbox, appelle list_files sans argument.
Si tu ne connais pas le chemin exact d'un fichier, appelle d'abord list_files au lieu de deviner.
Les données de l'utilisateur sont dans le sandbox : avant de répondre qu'une information est absente,
liste les fichiers et lis ceux qui semblent pertinents.
N'invente jamais le contenu d'un fichier : lis-le avec read_file.
Le client demande lui-même l'accord de l'utilisateur avant write_file, create_directory et move_file :
ne demande pas de confirmation dans ta réponse, appelle directement l'outil.
Réponds toujours en français, de façon concise."""


def ollama_chat(base_url: str, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Send one non-streaming chat request to Ollama's local HTTP API."""
    payload = json.dumps(
        {"model": model, "messages": messages, "tools": tools, "stream": False}
    ).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama a répondu HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(
            "Impossible de joindre Ollama. Vérifie qu'il est lancé et écoute sur "
            f"{base_url} (par exemple avec `ollama list`). Détail : {exc.reason}"
        ) from exc

    message = result.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("La réponse d'Ollama ne contient pas de message valide.")
    return message


def to_ollama_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    """Convert MCP tool descriptions to Ollama's function-tool format."""
    converted = []
    for tool in mcp_tools:
        schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "parameters": schema,
                },
            }
        )
    return converted


def parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, str):
        raw_arguments = json.loads(raw_arguments)
    if not isinstance(raw_arguments, dict):
        raise ValueError("Les arguments de l'outil doivent être un objet JSON.")
    return raw_arguments


def display_mutation(tool_name: str, arguments: dict[str, Any]) -> None:
    """Show the concrete proposed operation before asking the user."""
    print("\n" + "=" * 68)
    print(f"ACTION À CONFIRMER : {tool_name}")
    print("Chemin sandbox/ :", arguments.get("path", "(voir les détails ci-dessous)"))
    if tool_name == "write_file":
        mode = arguments.get("mode", "(non indiqué)")
        print("Mode :", mode)
        if mode == "overwrite":
            print("Effet : remplacera le contenu du fichier s'il existe déjà.")
        elif mode == "append":
            print("Effet : ajoutera le contenu à la fin du fichier.")
        content = arguments.get("content", "")
        if not isinstance(content, str):
            content = repr(content)
        print(f"Contenu proposé ({len(content.encode('utf-8'))} octets) :")
        print(repr(content))
    elif tool_name == "move_file":
        print("Source :", arguments.get("source", "(non indiquée)"))
        print("Destination :", arguments.get("destination", "(non indiquée)"))
        print("Effet : déplace ou renomme la source ; une destination existante est refusée.")
    elif tool_name == "create_directory":
        print("Effet : crée ce répertoire ainsi que les parents manquants.")
    print("Arguments reçus :")
    safe_arguments = {key: value for key, value in arguments.items() if key != "content"}
    print(json.dumps(safe_arguments, ensure_ascii=False, indent=2))
    print("L'outil est limité au répertoire sandbox/.")
    print("=" * 68)


def ask_consent(tool_name: str, arguments: dict[str, Any]) -> bool:
    display_mutation(tool_name, arguments)
    try:
        answer = input("Pour autoriser cette opération, tape exactement OUI : ").strip()
    except EOFError:
        return False
    return answer == "OUI"


def mcp_result_text(result: Any) -> str:
    parts = []
    for item in getattr(result, "content", []):
        if getattr(item, "type", None) == "text":
            parts.append(item.text)
        else:
            parts.append(f"[{getattr(item, 'type', 'contenu MCP')}] ")
    if getattr(result, "isError", False):
        return "Erreur renvoyée par le serveur MCP :\n" + "\n".join(parts)
    return "\n".join(parts) or json.dumps(
        getattr(result, "structuredContent", None), ensure_ascii=False
    )


async def run_chat(session: ClientSession, model: str, ollama_url: str, mcp_tools: list[Any]) -> None:
    ollama_tools = to_ollama_tools(mcp_tools)
    tools_by_name = {tool.name: tool for tool in mcp_tools}

    print(f"\nClient prêt. Modèle : {model} ; serveur MCP : sandbox-filesystem.")
    print("Écris /quit pour quitter. Les actions de modification demandent OUI dans le terminal.\n")

    while True:
        try:
            prompt = input("Toi > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nFin du client.")
            return
        if prompt.lower() in {"/quit", "/exit"}:
            return
        if not prompt:
            continue

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        for _ in range(MAX_TOOL_ROUNDS):
            # Called directly, not in a thread: Ctrl+C then interrupts the request at once.
            # Nothing else needs the event loop while the model is answering.
            assistant_message = ollama_chat(ollama_url, model, messages, ollama_tools)
            messages.append(assistant_message)
            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                content = (assistant_message.get("content") or "").strip()
                print(f"\n{model} > {content or '(réponse vide : ni texte ni appel d’outil reconnu)'}\n")
                break

            refused = False
            for call in tool_calls:
                function = call.get("function", {})
                tool_name = function.get("name")
                raw_arguments = function.get("arguments", {})
                try:
                    arguments = parse_arguments(raw_arguments)
                    if tool_name not in tools_by_name:
                        raise ValueError(f"Outil inconnu : {tool_name!r}.")

                    if tool_name in MUTATING_TOOLS:
                        # A refusal ends the whole request, otherwise the model keeps proposing it again.
                        if not ask_consent(tool_name, arguments):
                            refused = True
                            break
                        # Only the client sets this flag, and only after the user typed OUI.
                        arguments["confirmed"] = True
                        result = await session.call_tool(tool_name, arguments)
                        tool_output = mcp_result_text(result)
                    else:
                        print(f"Appel en lecture : {tool_name} {json.dumps(arguments, ensure_ascii=False)}")
                        result = await session.call_tool(tool_name, arguments)
                        tool_output = mcp_result_text(result)
                except Exception as exc:
                    tool_output = f"Appel d'outil impossible : {type(exc).__name__}: {exc}"

                print(f"Résultat de {tool_name or 'l’outil'} :\n{tool_output}\n")
                messages.append(
                    {"role": "tool", "tool_name": str(tool_name), "content": tool_output}
                )
            if refused:
                print("Opération refusée : aucun appel de modification n'a été envoyé, demande arrêtée.\n")
                break
        else:
            print(f"Le modèle a atteint la limite de {MAX_TOOL_ROUNDS} appels d'outils pour cette demande.\n")


def root_error(exc: BaseException) -> BaseException:
    """The MCP session (anyio) wraps errors in exception groups; return the underlying one."""
    while getattr(exc, "exceptions", None) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


async def main() -> None:
    # asyncio.run() replaces the Ctrl+C handler with one that waits for the next await,
    # so input() would ignore Ctrl+C. Restore the default: raise KeyboardInterrupt now.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    parser = argparse.ArgumentParser(description="Client de démonstration MCP avec Ollama local.")
    parser.add_argument("--model", default="qwen3:4b", help="Modèle Ollama (défaut : qwen3:4b).")
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434",
        help="Adresse locale de l'API Ollama.",
    )
    args = parser.parse_args()

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_DIR / "server.py")],
        cwd=PROJECT_DIR,
    )
    try:
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                print("Outils MCP découverts :", ", ".join(tool.name for tool in listed.tools))
                await run_chat(session, args.model, args.ollama_url, listed.tools)
    except Exception as exc:
        error = root_error(exc)
        if isinstance(error, McpError):
            error = f"le serveur MCP s'est arrêté ou n'a pas pu démarrer ({error})."
        print(f"Erreur de démarrage/transport : {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException as exc:
        if not isinstance(root_error(exc), KeyboardInterrupt):
            raise
        print("\nFin du client.")

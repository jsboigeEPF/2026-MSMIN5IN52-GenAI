"""Interactive MCP client that uses a local Ollama model for tool selection."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_DIR = Path(__file__).resolve().parent
MUTATING_TOOLS = {"write_file", "create_directory", "move_file"}
MAX_TOOL_ROUNDS = 8


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
    answer = input("Pour autoriser cette opération, tape exactement OUI : ").strip()
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

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        for _ in range(MAX_TOOL_ROUNDS):
            assistant_message = await asyncio.to_thread(
                ollama_chat, ollama_url, model, messages, ollama_tools
            )
            messages.append(assistant_message)
            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                print(f"\n{model} > {assistant_message.get('content', '')}\n")
                break

            for call in tool_calls:
                function = call.get("function", {})
                tool_name = function.get("name")
                raw_arguments = function.get("arguments", {})
                try:
                    arguments = parse_arguments(raw_arguments)
                    if tool_name not in tools_by_name:
                        raise ValueError(f"Outil inconnu : {tool_name!r}.")

                    if tool_name in MUTATING_TOOLS:
                        if not ask_consent(tool_name, arguments):
                            tool_output = "Opération refusée par l'utilisateur ; aucun appel MCP de modification n'a été envoyé."
                            print("Opération refusée.\n")
                        else:
                            # Only the client sets this flag, and only after the user typed OUI.
                            arguments["confirmed"] = True
                            result = await session.call_tool(tool_name, arguments)
                            tool_output = mcp_result_text(result)
                    else:
                        result = await session.call_tool(tool_name, arguments)
                        tool_output = mcp_result_text(result)
                except Exception as exc:
                    tool_output = f"Appel d'outil impossible : {type(exc).__name__}: {exc}"

                print(f"Résultat de {tool_name or 'l’outil'} :\n{tool_output}\n")
                messages.append(
                    {"role": "tool", "tool_name": str(tool_name), "content": tool_output}
                )
        else:
            print(f"Le modèle a atteint la limite de {MAX_TOOL_ROUNDS} appels d'outils pour cette demande.\n")


async def main() -> None:
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
    except (OSError, RuntimeError) as exc:
        print(f"Erreur de démarrage/transport : {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())

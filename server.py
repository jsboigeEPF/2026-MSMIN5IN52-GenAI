from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import StrictBool

PROJECT_DIR = Path(__file__).resolve().parent
ROOT = (PROJECT_DIR / "sandbox").resolve()
AUDIT_FILE = ROOT / ".mcp_audit.jsonl"
MAX_FILE_BYTES = 1_000_000
MAX_AUDIT_RESOURCE_BYTES = 100_000
MAX_LIST_ENTRIES = 1_000
_operation_lock = threading.RLock()

ROOT.mkdir(parents=True, exist_ok=True)
mcp = FastMCP("sandbox-filesystem")


class PathRejected(ValueError):
    """Raised when a requested path is outside the permitted workspace."""


def _safe_path(raw_path: str, *, allow_root: bool = False) -> Path:
    """Resolve a client path below ROOT and reject traversal and symlinks."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PathRejected("Le chemin doit être une chaîne non vide ('.' désigne la racine de sandbox/).")
    if "\x00" in raw_path or "\\" in raw_path:
        raise PathRejected("Le chemin contient un caractère interdit.")

    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or ".." in raw_path.split("/"):
        raise PathRejected(
            "Seuls les chemins relatifs à sandbox/ sont autorisés : écris par exemple "
            "'data/fichier.json', ou '.' pour la racine de sandbox/."
        )

    candidate = ROOT.joinpath(*relative.parts)
    if (candidate == ROOT and not allow_root) or candidate == AUDIT_FILE:
        raise PathRejected("Ce chemin est réservé ou désigne la racine.")

    # Symlinks could redirect an apparently local path outside sandbox/.
    current = ROOT
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise PathRejected("Les liens symboliques ne sont pas autorisés.")

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise PathRejected("Le chemin sort du répertoire autorisé.") from exc
    return resolved


def _audit(tool: str, arguments: dict[str, object], outcome: str, detail: str = "") -> None:
    """Append metadata only; file contents are never written to the audit log."""
    event: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "arguments": arguments,
        "outcome": outcome,
    }
    if detail:
        event["detail"] = detail[:300]
    line = json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
    with _operation_lock:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(AUDIT_FILE, flags, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)


def _require_confirmation(confirmed: bool) -> None:
    if confirmed is not True:
        raise PermissionError(
            "Consentement requis : le client doit demander l'accord de l'utilisateur, "
            "puis relancer avec confirmed=true."
        )


def _audit_arguments(**values: object) -> dict[str, object]:
    """Keep audit records bounded and avoid logging file contents."""
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, str):
            result[key] = value[:300]
        else:
            result[key] = value
    return result


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 file inside sandbox/. Absolute paths, traversal and symlinks are rejected."""
    args = _audit_arguments(path=path if isinstance(path, str) else repr(path))
    try:
        target = _safe_path(path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError("Le fichier demandé n'existe pas ou n'est pas un fichier.")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"Le fichier dépasse la limite de {MAX_FILE_BYTES} octets.")
        content = target.read_text(encoding="utf-8")
        _audit("read_file", args, "success")
        return content
    except Exception as exc:
        try:
            _audit("read_file", args, "denied_or_error", str(exc))
        except OSError:
            pass
        return f"Erreur read_file : {exc}"


@mcp.tool()
def list_files(path: str = ".", recursive: bool = True) -> str:
    """List files and directories inside sandbox/, recursively by default (whole tree, subdirectories included). path is relative to sandbox/: "." (the default) is its root; "/" and absolute paths are rejected."""
    args = _audit_arguments(path=path if isinstance(path, str) else repr(path), recursive=recursive)
    try:
        directory = _safe_path(path, allow_root=True)
        if not directory.exists() or not directory.is_dir():
            raise NotADirectoryError("Le chemin demandé n'est pas un répertoire.")

        entries: list[dict[str, object]] = []
        truncated = False
        pending = [directory]
        while pending:
            current = pending.pop()
            with os.scandir(current) as children:
                child_list = sorted(children, key=lambda child: child.name.casefold())
            subdirectories: list[Path] = []
            for child in child_list:
                child_path = Path(child.path)
                if child_path == AUDIT_FILE or child.name.startswith(".mcp-tmp-"):
                    continue
                if child.is_symlink():
                    continue
                if len(entries) >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
                is_directory = child.is_dir(follow_symlinks=False)
                relative_name = child_path.relative_to(ROOT).as_posix()
                entry: dict[str, object] = {
                    "path": relative_name,
                    "type": "directory" if is_directory else "file",
                }
                if not is_directory:
                    try:
                        entry["size_bytes"] = child.stat(follow_symlinks=False).st_size
                    except OSError:
                        entry["size_bytes"] = None
                entries.append(entry)
                if recursive and is_directory:
                    subdirectories.append(child_path)
            if truncated:
                break
            pending.extend(reversed(subdirectories))

        result = json.dumps(
            {"root": "sandbox/", "path": "." if directory == ROOT else directory.relative_to(ROOT).as_posix(),
             "recursive": recursive, "truncated": truncated, "entries": entries},
            ensure_ascii=False,
            indent=2,
        )
        _audit("list_files", args, "success", f"{len(entries)} entrées")
        return result
    except Exception as exc:
        try:
            _audit("list_files", args, "denied_or_error", str(exc))
        except OSError:
            pass
        return f"Erreur list_files : {exc}"


@mcp.tool()
def write_file(
    path: str,
    content: str,
    mode: Literal["overwrite", "append"],
    confirmed: StrictBool = False,
) -> str:
    """Create/replace or append to a UTF-8 file inside sandbox/. mode is required: "overwrite" replaces the file, "append" adds to its end. Requires confirmed=true."""
    args = _audit_arguments(
        path=path if isinstance(path, str) else repr(path),
        content_bytes=len(content.encode("utf-8")) if isinstance(content, str) else None,
        mode=mode,
        confirmed=confirmed,
    )
    try:
        _require_confirmation(confirmed)
        if mode not in ("overwrite", "append"):
            raise ValueError("mode doit être 'overwrite' ou 'append'.")
        if not isinstance(content, str):
            raise ValueError("Le contenu doit être du texte UTF-8.")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"Le contenu dépasse la limite de {MAX_FILE_BYTES} octets.")

        target = _safe_path(path)
        if not target.parent.is_dir():
            raise FileNotFoundError("Le répertoire parent doit déjà exister dans sandbox/.")

        _audit("write_file", args, "approved_attempt")
        with _operation_lock:
            if target.exists() and not target.is_file():
                raise IsADirectoryError("La destination existe et n'est pas un fichier.")
            bytes_written = len(data)
            if mode == "append":
                flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(target, flags, 0o600)
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ValueError("La destination doit être un fichier ordinaire.")
                    current_size = os.fstat(fd).st_size
                    separator = (
                        b"\n"
                        if current_size > 0 and os.pread(fd, 1, current_size - 1) != b"\n"
                        else b""
                    )
                    appended_data = separator + data
                    if current_size + len(appended_data) > MAX_FILE_BYTES:
                        raise ValueError(f"Le fichier dépasserait la limite de {MAX_FILE_BYTES} octets.")
                    os.write(fd, appended_data)
                    bytes_written = len(appended_data)
                finally:
                    os.close(fd)
            else:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".mcp-tmp-", delete=False) as stream:
                    temp = Path(stream.name)
                    stream.write(data)
                try:
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
        _audit("write_file", args, "success")
        action = "ajouté à" if mode == "append" else "écrit dans"
        return f"Contenu {action} sandbox/{target.relative_to(ROOT).as_posix()} ({bytes_written} octets)."
    except Exception as exc:
        try:
            _audit("write_file", args, "denied_or_error", str(exc))
        except OSError:
            pass
        return f"Erreur write_file : {exc}"


@mcp.tool()
def create_directory(path: str, confirmed: StrictBool = False) -> str:
    """Create a directory and any missing parent directories inside sandbox/. Requires confirmed=true."""
    args = _audit_arguments(
        path=path if isinstance(path, str) else repr(path),
        confirmed=confirmed,
    )
    try:
        _require_confirmation(confirmed)
        target = _safe_path(path)
        if target.exists() and not target.is_dir():
            raise FileExistsError("Un fichier existe déjà à cet emplacement.")

        _audit("create_directory", args, "approved_attempt")
        with _operation_lock:
            target.mkdir(parents=True, exist_ok=True)
        _audit("create_directory", args, "success")
        return f"Répertoire prêt : sandbox/{target.relative_to(ROOT).as_posix()}."
    except Exception as exc:
        try:
            _audit("create_directory", args, "denied_or_error", str(exc))
        except OSError:
            pass
        return f"Erreur create_directory : {exc}"


@mcp.tool()
def move_file(source: str, destination: str, confirmed: StrictBool = False) -> str:
    """Move or rename a file/directory inside sandbox/. Never replaces an existing destination; requires confirmed=true."""
    args = _audit_arguments(
        source=source if isinstance(source, str) else repr(source),
        destination=destination if isinstance(destination, str) else repr(destination),
        confirmed=confirmed,
    )
    try:
        _require_confirmation(confirmed)
        origin = _safe_path(source)
        target = _safe_path(destination)
        if not origin.exists():
            raise FileNotFoundError("La source n'existe pas.")
        if not target.parent.is_dir():
            raise FileNotFoundError("Le répertoire de destination doit déjà exister dans sandbox/.")
        if target.exists():
            raise FileExistsError("La destination existe déjà ; aucun écrasement n'est effectué.")
        if origin.is_dir() and target.is_relative_to(origin):
            raise ValueError("Un répertoire ne peut pas être déplacé à l'intérieur de lui-même.")

        _audit("move_file", args, "approved_attempt")
        with _operation_lock:
            if target.exists():
                raise FileExistsError("La destination est apparue avant le déplacement.")
            origin.rename(target)
        _audit("move_file", args, "success")
        return (
            f"Déplacement effectué : sandbox/{origin.relative_to(ROOT).as_posix()} → "
            f"sandbox/{target.relative_to(ROOT).as_posix()}. Pour annuler, déplace-le vers son chemin d'origine."
        )
    except Exception as exc:
        try:
            _audit("move_file", args, "denied_or_error", str(exc))
        except OSError:
            pass
        return f"Erreur move_file : {exc}"


@mcp.resource("filesystem://policy")
def filesystem_policy() -> str:
    """Read-only summary of the server's filesystem boundary and rules."""
    return (
        f"Répertoire autorisé : {ROOT}\n"
        "Les chemins sont relatifs à sandbox/. Les chemins absolus, '..', les liens symboliques, "
        "les fichiers de plus de 1 000 000 octets et l'accès direct au journal d'audit sont interdits. "
        "write_file, create_directory et move_file exigent confirmed=true. write_file propose les modes overwrite et append. "
        "Le client doit obtenir l'accord de l'utilisateur avant les modifications."
    )


@mcp.resource("filesystem://manifest")
def filesystem_manifest() -> str:
    """List top-level files and directories visible to the tools."""
    entries = []
    for item in sorted(ROOT.iterdir(), key=lambda path: path.name.casefold()):
        if item == AUDIT_FILE or item.is_symlink():
            continue
        entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
    return json.dumps({"root": "sandbox/", "entries": entries}, ensure_ascii=False, indent=2)


@mcp.resource("filesystem://audit/recent")
def recent_audit_log() -> str:
    """Read recent audit metadata; file contents are never logged."""
    if not AUDIT_FILE.exists():
        return "[]"
    data = AUDIT_FILE.read_bytes()[-MAX_AUDIT_RESOURCE_BYTES:]
    lines = data.decode("utf-8", errors="replace").splitlines()
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # The first line may be partial when the byte limit cuts through it.
            continue
        if isinstance(event, dict):
            events.append(event)
    return json.dumps(events[-100:], ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")

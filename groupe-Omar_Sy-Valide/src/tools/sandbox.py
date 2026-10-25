"""Run pytest and ruff on a run worktree (SPEC §4.4).

The code under test has been read and approved by a human at the write gate before it ever runs, so the
default is a local subprocess: no API keys or tokens in its environment (allowlist), a time limit, and the
user's working tree untouched (it runs in the worktree). `docker` adds network and filesystem isolation for
those who want it; it fails closed if Docker or the image is missing.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

SandboxMode = Literal["subprocess", "docker"]


class SandboxUnavailableError(RuntimeError):
    pass


# The only host variables a subprocess-mode run inherits: no API keys, tokens or credentials.
_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LANG",
)
_MAX_OUTPUT_CHARS = 20_000
_PYTEST_ARGS = ("-m", "pytest", "-q", "-p", "no:cacheprovider")
_PYTEST_OK = (0, 5)  # 5 = no tests collected: a repository without tests has nothing to fail
# EXE001/EXE002 check the executable bit, which the agent neither generates nor commits, and which
# Docker Desktop bind mounts report as set on every file.
_RUFF_ARGS = ("check", "--no-cache", "--extend-ignore", "EXE001,EXE002", "--output-format", "json")


@dataclass(frozen=True)
class _Result:
    code: int | None  # None: timed out
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        return (self.stdout + self.stderr)[-_MAX_OUTPUT_CHARS:]


def _violations(result: _Result) -> list[dict[str, Any]] | None:
    """Ruff's JSON report, or None if ruff itself failed (bad config, crash, timeout)."""
    if result.code not in (0, 1):
        return None
    try:
        data: object = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return cast(list[dict[str, Any]], data) if isinstance(data, list) else None


def _code(violation: dict[str, Any]) -> str:
    return str(violation.get("code") or "syntax-error")


def _describe(file: str, violation: dict[str, Any]) -> str:
    location = cast(dict[str, Any], violation.get("location") or {})
    where = f"{file}:{location.get('row', '?')}:{location.get('column', '?')}"
    return f"{where}: {_code(violation)} {violation.get('message', '')}"


def docker_available(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        done = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _sandbox_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOWLIST}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


class SandboxRunner:
    def __init__(self, mode: SandboxMode, timeout_sec: int, image: str) -> None:
        if mode == "docker" and not docker_available(image):
            raise SandboxUnavailableError(
                f"SANDBOX_MODE=docker needs Docker running and the image '{image}' "
                "(docker build -t mini-opencode-sandbox sandbox/); leave SANDBOX_MODE empty for local runs."
            )
        self.mode: SandboxMode = mode
        self.timeout_sec = timeout_sec
        self.image = image

    async def run_pytest(self, worktree_path: Path) -> tuple[bool, str]:
        if self.mode == "docker":
            result = await self._run_docker(worktree_path, ["python", *_PYTEST_ARGS])
        else:
            result = await self._run([sys.executable, *_PYTEST_ARGS], cwd=worktree_path, env=_sandbox_env())
        if result.code == 5:
            return True, result.text + "\n(no tests in the repository: nothing to run)"
        return result.code in _PYTEST_OK, result.text

    async def run_linter(
        self, worktree_path: Path, file: str, baseline: str | None = None
    ) -> tuple[bool, str]:
        """Ruff with the target repository's own configuration. With `baseline` (the file before the run),
        only violations the change introduces count: the agent is not blamed for code it did not write."""
        target = str(PurePosixPath(Path(file).as_posix()))
        after_run = await self._ruff(worktree_path, [target])
        after = _violations(after_run)
        if after is None:
            return False, after_run.text or f"ruff failed (exit code {after_run.code})"
        before: list[dict[str, Any]] = []
        if baseline is not None:
            before = (
                _violations(await self._ruff(worktree_path, ["--stdin-filename", target, "-"], baseline))
                or []
            )
        remaining = Counter(_code(v) for v in before)
        new: list[dict[str, Any]] = []
        for violation in after:
            if remaining[_code(violation)] > 0:
                remaining[_code(violation)] -= 1
            else:
                new.append(violation)
        lines = [_describe(target, v) for v in new]
        if ignored := len(after) - len(new):
            lines.append(f"({ignored} pre-existing violation(s) ignored: they were already in the file)")
        return not new, "\n".join(lines) or "All checks passed!"

    async def _ruff(self, worktree_path: Path, args: list[str], stdin: str | None = None) -> _Result:
        if self.mode == "docker":
            return await self._run_docker(worktree_path, ["ruff", *_RUFF_ARGS, *args], stdin)
        cmd = [sys.executable, "-m", "ruff", *_RUFF_ARGS, *args]
        return await self._run(cmd, cwd=worktree_path, env=_sandbox_env(), stdin=stdin)

    def _docker_cmd(
        self, worktree_path: Path, cmd: list[str], name: str, interactive: bool = False
    ) -> list[str]:
        return [
            "docker",
            "run",
            *(["-i"] if interactive else []),
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "-v",
            f"{worktree_path}:/work:ro",  # tested code must not alter what gets reviewed and committed
            "-w",
            "/work",
            self.image,
            *cmd,
        ]

    async def _run_docker(self, worktree_path: Path, cmd: list[str], stdin: str | None = None) -> _Result:
        name = f"mini-opencode-{uuid.uuid4().hex[:12]}"
        docker_cmd = self._docker_cmd(worktree_path, cmd, name, interactive=stdin is not None)
        result = await self._run(docker_cmd, cwd=None, env=None, stdin=stdin)
        if result.code is None:
            subprocess.run(["docker", "kill", name], capture_output=True)
        return result

    async def _run(
        self, cmd: list[str], cwd: Path | None, env: dict[str, str] | None, stdin: str | None = None
    ) -> _Result:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        data = stdin.encode("utf-8") if stdin is not None else None
        try:
            out, err = await asyncio.wait_for(proc.communicate(data), timeout=self.timeout_sec)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return _Result(
                None, "", f"TIMEOUT: command timed out after {self.timeout_sec}s: {' '.join(cmd[-4:])}"
            )
        return _Result(proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))

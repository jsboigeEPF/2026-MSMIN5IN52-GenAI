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

from src.tools.checks import Check

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


def _key(violation: dict[str, Any]) -> tuple[str, str]:
    return _code(violation), str(violation.get("message", ""))


async def _no_baseline() -> _Result:
    return _Result(0, "[]", "")


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

    async def project_python(self, repo: Path) -> Path | None:
        """The target project's own virtualenv, if it has pytest: its dependencies (fastapi...) are then
        importable by the tests. None (agent's interpreter) in docker mode or without a usable venv.
        The probe runs like the tests: stripped environment, time limit."""
        if self.mode == "docker":
            return None
        for candidate in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe"):
            python = repo / candidate
            if python.exists():
                probe = await self._run([str(python), "-c", "import pytest"], cwd=repo, env=_sandbox_env())
                if probe.code == 0:
                    return python
        return None

    async def run_command(self, worktree_path: Path, check: Check) -> tuple[bool, str]:
        """A project's own test or lint command (npm test, go vet...), run in the worktree like pytest is."""
        if self.mode == "docker":
            raise SandboxUnavailableError(
                f"SANDBOX_MODE=docker only has Python tools; '{check.label}' needs the local mode"
            )
        env = {**_sandbox_env(), "CI": "true"}  # test runners stay non-interactive (no watch mode)
        result = await self._run(list(check.argv), cwd=worktree_path / check.cwd, env=env)
        return result.code == 0, result.text or f"{check.label}: exit code {result.code}"

    async def run_pytest(
        self, worktree_path: Path, allow_no_tests: bool = False, python: Path | None = None
    ) -> tuple[bool, str]:
        """`allow_no_tests`: pytest exit code 5 (nothing collected) passes, for a repository that has no
        tests at all. Off by default, so a change that loses every test does not pass by accident.
        `python`: the target project's interpreter, so its dependencies are importable (subprocess mode)."""
        if self.mode == "docker":
            result = await self._run_docker(worktree_path, ["python", *_PYTEST_ARGS])
        else:
            interpreter = str(python or sys.executable)
            result = await self._run([interpreter, *_PYTEST_ARGS], cwd=worktree_path, env=_sandbox_env())
        if result.code == 5 and allow_no_tests:
            return True, result.text + "\n(no tests in the repository: nothing to run)"
        return result.code == 0, result.text

    async def run_linter(
        self, worktree_path: Path, file: str, baseline: str | None = None
    ) -> tuple[bool, str]:
        """Ruff with the target repository's own configuration. With `baseline` (the file before the run),
        only violations the change introduces count: the agent is not blamed for code it did not write."""
        target = str(PurePosixPath(Path(file).as_posix()))
        stdin_args = ["--stdin-filename", target, "-"]
        after_run, before_run = await asyncio.gather(
            self._ruff(worktree_path, [target]),
            self._ruff(worktree_path, stdin_args, baseline) if baseline is not None else _no_baseline(),
        )
        after = _violations(after_run)
        if after is None:
            return False, after_run.text or f"ruff failed (exit code {after_run.code})"
        # Matched on rule and message (which names the symbol), not line numbers: the change moves lines.
        remaining = Counter(_key(v) for v in _violations(before_run) or [])
        new: list[dict[str, Any]] = []
        for violation in after:
            if remaining[_key(violation)] > 0:
                remaining[_key(violation)] -= 1
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

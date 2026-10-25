"""Run pytest and ruff on a run worktree (SPEC §4.4).

The code under test has been read and approved by a human at the write gate before it ever runs, so the
default is a local subprocess: no API keys or tokens in its environment (allowlist), a time limit, and the
user's working tree untouched (it runs in the worktree). `docker` adds network and filesystem isolation for
those who want it; it fails closed if Docker or the image is missing.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal

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
_RUFF_ARGS = ("check", "--no-cache", "--extend-ignore", "EXE001,EXE002")


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
            return await self._run_docker(worktree_path, ["python", *_PYTEST_ARGS])
        return await self._run([sys.executable, *_PYTEST_ARGS], cwd=worktree_path, env=_sandbox_env())

    async def run_linter(self, worktree_path: Path, file: str) -> tuple[bool, str]:
        target = str(PurePosixPath(Path(file).as_posix()))
        if self.mode == "docker":
            return await self._run_docker(worktree_path, ["ruff", *_RUFF_ARGS, target])
        return await self._run(
            [sys.executable, "-m", "ruff", *_RUFF_ARGS, target],
            cwd=worktree_path,
            env=_sandbox_env(),
        )

    def _docker_cmd(self, worktree_path: Path, cmd: list[str], name: str) -> list[str]:
        return [
            "docker",
            "run",
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

    async def _run_docker(self, worktree_path: Path, cmd: list[str]) -> tuple[bool, str]:
        name = f"mini-opencode-{uuid.uuid4().hex[:12]}"
        passed, output = await self._run(self._docker_cmd(worktree_path, cmd, name), cwd=None, env=None)
        if output.startswith("TIMEOUT"):
            subprocess.run(["docker", "kill", name], capture_output=True)
        return passed, output

    async def _run(self, cmd: list[str], cwd: Path | None, env: dict[str, str] | None) -> tuple[bool, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_sec)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"TIMEOUT: command timed out after {self.timeout_sec}s: {' '.join(cmd[-4:])}"
        text = out.decode("utf-8", errors="replace")[-_MAX_OUTPUT_CHARS:]
        return proc.returncode == 0, text

"""Run pytest and ruff on a worktree, isolated from the host (SPEC §4.4).

Fails closed: `auto` never silently downgrades to running generated code on the host. Host execution
needs an explicit SANDBOX_MODE=subprocess and is logged as a warning on every run.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal

SandboxMode = Literal["docker", "subprocess"]

logger = logging.getLogger(__name__)
_HOST_WARNING = "SANDBOX_MODE=subprocess: generated code runs on the host without isolation"


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
    def __init__(self, mode: Literal["auto", "docker", "subprocess"], timeout_sec: int, image: str) -> None:
        if mode == "auto":
            if not docker_available(image):
                raise SandboxUnavailableError(
                    f"SANDBOX_MODE=auto needs Docker and the image '{image}' "
                    "(docker build -t mini-opencode-sandbox sandbox/). To run generated code on the host "
                    "instead, set SANDBOX_MODE=subprocess explicitly."
                )
            mode = "docker"
        self.mode: SandboxMode = mode
        if self.mode == "subprocess":
            logger.warning(_HOST_WARNING)
        self.timeout_sec = timeout_sec
        self.image = image

    async def run_pytest(self, worktree_path: Path) -> tuple[bool, str]:
        if self.mode == "docker":
            return await self._run_docker(worktree_path, ["python", *_PYTEST_ARGS])
        logger.warning(_HOST_WARNING)
        return await self._run([sys.executable, *_PYTEST_ARGS], cwd=worktree_path, env=_sandbox_env())

    async def run_linter(self, worktree_path: Path, file: str) -> tuple[bool, str]:
        target = str(PurePosixPath(Path(file).as_posix()))
        if self.mode == "docker":
            return await self._run_docker(worktree_path, ["ruff", "check", "--no-cache", target])
        logger.warning(_HOST_WARNING)
        return await self._run(
            [sys.executable, "-m", "ruff", "check", "--no-cache", target],
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
            f"{worktree_path}:/work",
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

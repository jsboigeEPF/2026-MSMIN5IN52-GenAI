"""Which tests and linter verify a change, chosen from the target file and the project around it (SPEC §4.4).

- `.py` file: pytest and ruff (baseline-aware), as for any Python repository;
- file inside a Node project (nearest `package.json`): its `test` and `lint` npm scripts, if defined;
- `.go` file in a Go module: `go test ./...` and `go vet ./...`; `.rs` file in a Cargo crate: `cargo test`;
- anything else: nothing to run, said explicitly.

A `.omar.toml` at the repository root overrides the detection:

    [checks]
    cwd = "frontend"         # where to run, relative to the repository root
    test = "npm test"        # "" disables the tests
    lint = "npm run lint"    # "" disables the linter
"""

import json
import shlex
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

CONFIG_FILE = ".omar.toml"
_NPM_DEFAULT_TEST = "no test specified"  # the script `npm init` writes, which always fails


@dataclass(frozen=True)
class Check:
    label: str  # shown to the reviewer: "pytest", "npm test", ...
    kind: Literal["pytest", "ruff", "command"]  # pytest and ruff keep their dedicated runners
    argv: tuple[str, ...] = ()
    cwd: str = "."  # relative to the repository root (POSIX)


@dataclass(frozen=True)
class CheckPlan:
    tests: Check | None
    lint: Check | None
    note: str | None = None  # why something is not checked
    node_modules: str | None = None  # directory whose node_modules the worktree must share


def _nearest(repo: Path, start: PurePosixPath, marker: str) -> PurePosixPath | None:
    """The closest directory from `start` up to the repository root that contains `marker`."""
    for directory in (start, *start.parents):
        if (repo / directory / marker).is_file():
            return directory
    return None


def _command(text: str, cwd: str) -> Check | None:
    argv = tuple(shlex.split(text))
    return Check(label=text, kind="command", argv=argv, cwd=cwd) if argv else None


def _from_config(repo: Path) -> CheckPlan | None:
    path = repo / CONFIG_FILE
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = cast(dict[str, Any], data.get("checks", {}))
    cwd = str(section.get("cwd", "."))
    tests = _command(str(section.get("test", "")), cwd)
    lint = _command(str(section.get("lint", "")), cwd)
    node = cwd if (repo / cwd / "package.json").is_file() else None
    return CheckPlan(tests, lint, None if tests or lint else f"{CONFIG_FILE} disables every check", node)


def _npm(repo: Path, project: PurePosixPath) -> CheckPlan:
    cwd = project.as_posix() or "."
    if shutil.which("npm") is None:
        return CheckPlan(None, None, "npm is not installed: the Node project cannot be tested here")
    manifest = cast(dict[str, Any], json.loads((repo / project / "package.json").read_text(encoding="utf-8")))
    scripts = cast(dict[str, str], manifest.get("scripts") or {})
    test_script = scripts.get("test", "")
    tests = None
    if test_script and _NPM_DEFAULT_TEST not in test_script:
        tests = Check(label="npm test", kind="command", argv=("npm", "test", "--silent"), cwd=cwd)
    lint = None
    if "lint" in scripts:
        lint = Check(label="npm run lint", kind="command", argv=("npm", "run", "lint", "--silent"), cwd=cwd)
    note = None if tests or lint else f"{cwd}/package.json defines no test or lint script"
    return CheckPlan(tests, lint, note, node_modules=cwd)


def plan_checks(repo: Path, target_file: str) -> CheckPlan:
    if (configured := _from_config(repo)) is not None:
        return configured
    target = PurePosixPath(target_file)
    suffix = target.suffix.lower()
    if suffix in (".py", ".pyi"):
        return CheckPlan(Check("pytest", "pytest"), Check("ruff", "ruff"))
    if suffix == ".go" and (module := _nearest(repo, target.parent, "go.mod")) is not None:
        cwd = module.as_posix() or "."
        return CheckPlan(
            Check("go test", "command", ("go", "test", "./..."), cwd),
            Check("go vet", "command", ("go", "vet", "./..."), cwd),
        )
    if suffix == ".rs" and (crate := _nearest(repo, target.parent, "Cargo.toml")) is not None:
        return CheckPlan(
            Check("cargo test", "command", ("cargo", "test", "--quiet"), crate.as_posix() or "."), None
        )
    if (project := _nearest(repo, target.parent, "package.json")) is not None:
        return _npm(repo, project)
    return CheckPlan(None, None, f"no tests or linter known for {target.name}: nothing to run")


def link_node_modules(repo: Path, worktree: Path, plan: CheckPlan) -> None:
    """Share the user's installed dependencies with the worktree (node_modules is never committed)."""
    if plan.node_modules is None:
        return
    source = repo / plan.node_modules / "node_modules"
    link = worktree / plan.node_modules / "node_modules"
    if source.is_dir() and not link.exists():
        link.symlink_to(source, target_is_directory=True)

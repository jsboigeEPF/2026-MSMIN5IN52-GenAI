"""Which checks verify a change, by language and project (tools/checks.py)."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from src.tools.checks import Check, CheckPlan, link_node_modules, plan_checks
from src.tools.sandbox import SandboxRunner


def package(repo: Path, directory: str, scripts: dict[str, str]) -> None:
    (repo / directory).mkdir(parents=True, exist_ok=True)
    (repo / directory / "package.json").write_text(json.dumps({"name": "front", "scripts": scripts}))


def test_python_files_get_pytest_and_ruff(tmp_path: Path) -> None:
    plan = plan_checks(tmp_path, "api/main.py")
    assert (plan.tests, plan.lint) == (Check("pytest", "pytest"), Check("ruff", "ruff"))


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
def test_a_css_file_uses_the_npm_scripts_of_its_project(tmp_path: Path) -> None:
    package(tmp_path, "frontend", {"test": "vitest run", "lint": "next lint", "build": "next build"})
    plan = plan_checks(tmp_path, "frontend/app/globals.css")
    assert plan.tests == Check("npm test", "command", ("npm", "test", "--silent"), "frontend")
    assert plan.lint == Check("npm run lint", "command", ("npm", "run", "lint", "--silent"), "frontend")
    assert plan.node_modules == "frontend"


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm not installed")
def test_the_npm_init_placeholder_test_script_is_not_a_test(tmp_path: Path) -> None:
    package(tmp_path, ".", {"test": 'echo "Error: no test specified" && exit 1'})
    plan = plan_checks(tmp_path, "src/app.ts")
    assert plan.tests is None and plan.lint is None
    assert plan.note is not None and "no test or lint script" in plan.note


def test_a_file_with_no_known_tooling_is_not_checked(tmp_path: Path) -> None:
    plan = plan_checks(tmp_path, "docs/guide.md")
    assert plan == CheckPlan(None, None, "no tests or linter known for guide.md: nothing to run")


def test_omar_toml_overrides_the_detection(tmp_path: Path) -> None:
    (tmp_path / ".omar.toml").write_text('[checks]\ncwd = "web"\ntest = "make test"\nlint = ""\n')
    plan = plan_checks(tmp_path, "web/style.css")
    assert plan.tests == Check("make test", "command", ("make", "test"), "web") and plan.lint is None


def test_go_and_rust_projects(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "go.mod").write_text("module svc\n")
    go = plan_checks(tmp_path, "svc/handler.go")
    assert go.tests is not None and go.tests.argv == ("go", "test", "./...") and go.tests.cwd == "svc"
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    rust = plan_checks(tmp_path, "src/main.rs")
    assert rust.tests is not None and rust.tests.label == "cargo test" and rust.lint is None


def test_node_modules_are_shared_with_the_worktree(tmp_path: Path) -> None:
    repo, worktree = tmp_path / "repo", tmp_path / "wt"
    (repo / "frontend" / "node_modules").mkdir(parents=True)
    (worktree / "frontend").mkdir(parents=True)
    link_node_modules(repo, worktree, CheckPlan(None, None, node_modules="frontend"))
    assert (worktree / "frontend" / "node_modules").resolve() == (repo / "frontend" / "node_modules")


async def test_the_projects_own_virtualenv_runs_the_tests(tmp_path: Path) -> None:
    runner = SandboxRunner("subprocess", 30, "unused")
    assert await runner.project_python(tmp_path) is None
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')  # stands for a venv that has pytest
    python.chmod(0o755)
    assert await runner.project_python(tmp_path) == python

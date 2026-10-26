import json
from pathlib import Path

import httpx2
import pytest
from typer.testing import CliRunner

from src.cli import app, fetch_key_info
from src.config import Settings
from src.llm.client import ChatRequest, ChatResponse, LLMProviderError
from src.llm.fake import FakeLLM
from tests.conftest import FIXED_CALC

cli = CliRunner()


@pytest.fixture(autouse=True)
def isolated_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No real .env is read (cwd has none) and tests never need Docker or a key."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "subprocess")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def invoke(*args: str | Path) -> str:
    result = cli.invoke(app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result.output


def test_mock_run_end_to_end_through_the_cli(git_repo: Path, tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "planner": [{"target_file": "calc.py", "steps": ["fix add"]}],
                "coder": [{"new_content": FIXED_CALC, "rationale": "r"}],
            }
        )
    )
    db = tmp_path / "cli.db"

    out = invoke(
        "start", "--db", db, "--mock-llm", "--mock-script", script,
        "--repo", git_repo, "--prompt", "Fix add()", "--run-id", "cli-1",
    )  # fmt: skip
    assert "AWAITING_APPROVAL" in out and "plan gate" in out and "1. fix add" in out
    assert ":: calc.py" in out  # chosen by the (fake) planner

    out = invoke(
        "decide", "cli-1", "--db", db, "--mock-llm", "--mock-script", script,
        "--action", "approve", "--actor", "alice",
    )  # fmt: skip
    assert "write gate" in out and "+    return a + b" in out

    out = invoke("status", "cli-1", "--db", db)
    assert "write gate" in out and "cost so far" in out

    refused = cli.invoke(
        app, ["decide", "cli-1", "--db", str(db), "--mock-llm", "--action", "revise", "--actor", "a"]
    )
    assert refused.exit_code == 1 and "comment" in refused.output

    out = invoke("decide", "cli-1", "--db", db, "--mock-llm", "--action", "approve", "--actor", "alice")
    assert "COMPLETED" in out and "pytest PASSED" in out
    assert (git_repo / "calc.py").read_text() == FIXED_CALC

    assert "restored calc.py" in invoke("undo", "cli-1", "--db", db)
    assert (git_repo / "calc.py").read_text() != FIXED_CALC

    assert "cli-1" in invoke("runs", "--db", db)
    assert json.loads(invoke("report", "--db", db))["runs"] == 1
    assert "no run expired" in invoke("sweep", "--db", db)


def test_unknown_run_is_a_clean_error(tmp_path: Path) -> None:
    result = cli.invoke(app, ["status", "nope", "--db", str(tmp_path / "x.db")])
    assert result.exit_code == 1 and "unknown run nope" in result.output


def test_live_mode_without_settings_is_a_clean_error(git_repo: Path, tmp_path: Path) -> None:
    result = cli.invoke(
        app,
        [
            "start",
            "--db",
            str(tmp_path / "x.db"),
            "--repo",
            str(git_repo),
            "--file",
            "calc.py",
            "--prompt",
            "p",
        ],
    )
    assert result.exit_code == 1 and "OPENROUTER_API_KEY" in result.output


async def test_fetch_key_info_reads_usage_without_exposing_the_key() -> None:
    seen: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(
            200, json={"data": {"label": "sk-or-v1-abc...xyz", "usage": 0.42, "limit": 5.0}}
        )

    settings = Settings(_env_file=None, OPENROUTER_API_KEY="sk-or-secret")
    info = await fetch_key_info(settings, httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))

    assert str(seen[0].url) == "https://openrouter.ai/api/v1/key"
    assert seen[0].headers["Authorization"] == "Bearer sk-or-secret"
    assert info == {"label": "sk-or-v1-abc...xyz", "usage": 0.42, "limit": 5.0}
    assert "sk-or-secret" not in json.dumps(info)


def test_provider_error_is_one_line_with_a_resume_hint(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def blocked(self: FakeLLM, request: ChatRequest) -> ChatResponse:
        raise LLMProviderError("planner (vendor/m): HTTP 404: Model blocked by guardrail")

    monkeypatch.setattr(FakeLLM, "complete", blocked)
    result = cli.invoke(
        app,
        ["start", "--db", str(tmp_path / "x.db"), "--mock-llm", "--repo", str(git_repo), "--file", "calc.py"]
        + ["--prompt", "p", "--run-id", "blocked-1"],
    )
    assert result.exit_code == 1
    assert "HTTP 404: Model blocked by guardrail" in result.output
    assert "resume blocked-1" in result.output
    assert "Traceback" not in result.output

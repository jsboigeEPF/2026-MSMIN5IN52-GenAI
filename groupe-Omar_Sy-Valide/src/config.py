"""Runtime settings, read from the environment and `.env`. Model slugs live only there, never in code."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.process.graph import Limits

PROJECT_DIR = Path(__file__).resolve().parents[1]  # groupe-Omar_Sy-Valide/
ENV_FILE = PROJECT_DIR / ".env"  # read from the project, whatever repository the agent is launched in
DEFAULT_DB = PROJECT_DIR / "omar-sy-valide.db"  # one journal for every target repository


class Settings(BaseSettings):
    # Empty values (as copied from .env.example) fall back to the defaults below.
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", env_ignore_empty=True)

    OPENROUTER_API_KEY: SecretStr | None = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    PLANNER_MODEL: str = ""
    CODER_MODEL: str = ""
    SUMMARIZER_MODEL: str = ""

    USD_EUR_RATE: float = Field(default=0.92, gt=0)
    MAX_COST_EUR_PER_RUN: float = Field(default=0.5, gt=0)
    BUDGET_EUR_TOTAL: float = Field(default=10.0, gt=0)

    APPROVAL_TIMEOUT_SEC: int = Field(default=3600, gt=0)
    MAX_FIX: int = Field(default=10, ge=0)  # automatic fixes before the human decides
    MAX_REVISIONS: int = Field(default=5, ge=0)  # shared by the plan and write gates

    # "subprocess": local run of human-approved code, secrets stripped from its environment.
    # "docker": adds network/filesystem isolation; refuses to run if Docker or the image is missing.
    SANDBOX_MODE: Literal["subprocess", "docker"] = "subprocess"
    SANDBOX_TIMEOUT_SEC: int = Field(default=120, gt=0)
    SANDBOX_IMAGE: str = "mini-opencode-sandbox:latest"  # docker mode only, built from sandbox/Dockerfile

    @field_validator("SANDBOX_MODE", mode="before")
    @classmethod
    def _legacy_auto(cls, value: object) -> object:
        """`auto` (Docker required) predates the write gate; local runs are now the default."""
        return "subprocess" if value == "auto" else value

    def limits(self) -> Limits:
        return Limits(max_fix=self.MAX_FIX, max_revisions=self.MAX_REVISIONS)

    def usd_to_eur(self, usd: float) -> float:
        return usd * self.USD_EUR_RATE

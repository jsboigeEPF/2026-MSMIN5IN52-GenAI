"""Runtime settings, read from the environment and `.env`. Model slugs live only there, never in code."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Empty values (as copied from .env.example) fall back to the defaults below.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True)

    OPENROUTER_API_KEY: SecretStr | None = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    PLANNER_MODEL: str = ""
    CODER_MODEL: str = ""
    SUMMARIZER_MODEL: str = ""

    USD_EUR_RATE: float = Field(default=0.92, gt=0)
    MAX_COST_EUR_PER_RUN: float = Field(default=0.5, gt=0)
    BUDGET_EUR_TOTAL: float = Field(default=10.0, gt=0)

    APPROVAL_TIMEOUT_SEC: int = Field(default=3600, gt=0)
    MAX_FIX: int = Field(default=2, ge=0)
    MAX_REVISIONS: int = Field(default=2, ge=0)

    # "auto" = docker when available, subprocess otherwise (resolved by the sandbox).
    SANDBOX_MODE: Literal["auto", "docker", "subprocess"] = "auto"
    SANDBOX_TIMEOUT_SEC: int = Field(default=120, gt=0)
    SANDBOX_IMAGE: str = "mini-opencode-sandbox:latest"  # built from sandbox/Dockerfile

    def usd_to_eur(self, usd: float) -> float:
        return usd * self.USD_EUR_RATE

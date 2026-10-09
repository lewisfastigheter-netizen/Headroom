"""Settings and configuration loading."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CACHE_DIR = DATA_DIR / "cache"

# Set HTTP_CONTACT in .env (an email or repository URL) so site owners can reach you.
USER_AGENT = "Headroom/0.1 (personal research project; Nordic property credit monitor)"


class Settings(BaseSettings):
    """Runtime settings, read from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_prefix="", extra="ignore", case_sensitive=False
    )

    headroom_mode: Literal["demo", "live", "auto"] = "auto"
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: str | None = None
    # Model IDs checked against platform.claude.com/docs/en/models/overview (Oct 2026).
    anthropic_model: str = "claude-sonnet-5-5"  # report and covenant extraction
    anthropic_fast_model: str = "claude-haiku-5-5"  # event classification
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    openai_fast_model: str = "gpt-4.1-mini"
    bolagsverket_client_id: str | None = None
    bolagsverket_client_secret: str | None = None
    bolagsverket_bulkfile: str | None = None  # path to the downloaded bulk file (zip or txt)
    http_contact: str | None = None  # appended to the user agent if set

    @property
    def llm_available(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.openai_api_key)


@lru_cache
def settings() -> Settings:
    return Settings()


def weights() -> dict[str, Any]:
    """Read on every call so edits to weights.yaml apply on the next app rerun."""
    with open(CONFIG_DIR / "weights.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_mode() -> Literal["demo", "live"]:
    """`auto` uses live snapshots when present, otherwise demo."""
    mode = settings().headroom_mode
    if mode == "auto":
        live = SNAPSHOT_DIR / "live" / "company.parquet"
        return "live" if live.exists() else "demo"
    return mode

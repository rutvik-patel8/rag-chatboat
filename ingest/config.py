"""Settings shared by every ingestion step, loaded from the repo-root .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("GEMINI_API_KEY", "DATABASE_URL", "GEMINI_CHAT_MODEL", "GEMINI_EMBED_MODEL", "EMBED_DIM")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    database_url: str
    chat_model: str
    embed_model: str
    embed_dim: int


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv(REPO_ROOT / ".env")
        env = os.environ
    missing = [key for key in REQUIRED if not env.get(key, "").strip()]
    if missing:
        raise ConfigError(f"Missing required settings in .env: {', '.join(missing)} (see .env.example)")
    try:
        dim = int(env["EMBED_DIM"])
    except ValueError:
        raise ConfigError(f"EMBED_DIM must be an integer, got {env['EMBED_DIM']!r}") from None
    if not 128 <= dim <= 3072:
        raise ConfigError(f"EMBED_DIM must be between 128 and 3072, got {dim}")
    return Settings(
        gemini_api_key=env["GEMINI_API_KEY"].strip(),
        database_url=env["DATABASE_URL"].strip(),
        chat_model=env["GEMINI_CHAT_MODEL"].strip(),
        embed_model=env["GEMINI_EMBED_MODEL"].strip(),
        embed_dim=dim,
    )

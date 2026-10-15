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
    embed_per_minute: int = 100  # Gemini free tier counts each embedded text as one request
    embed_tokens_per_minute: int = 20000  # free tier refuses ~25–30k embedding tokens in one minute
    chat_per_minute: int = 10  # generation calls (section summaries); raise on a paid plan
    summary_model: str = ""  # model for ingest-time summaries; empty means chat_model
    vision_model: str = ""  # model for page transcription (visual profile); empty means summary_model


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
    embed_per_minute = _positive_int(env, "EMBED_REQUESTS_PER_MINUTE", 100)
    chat_per_minute = _positive_int(env, "GEMINI_CHAT_REQUESTS_PER_MINUTE", 10)
    embed_tokens_per_minute = _positive_int(env, "EMBED_TOKENS_PER_MINUTE", 20000)
    summary_model = env.get("GEMINI_SUMMARY_MODEL", "").strip() or env["GEMINI_CHAT_MODEL"].strip()
    return Settings(
        gemini_api_key=env["GEMINI_API_KEY"].strip(),
        database_url=env["DATABASE_URL"].strip(),
        chat_model=env["GEMINI_CHAT_MODEL"].strip(),
        embed_model=env["GEMINI_EMBED_MODEL"].strip(),
        embed_dim=dim,
        embed_per_minute=embed_per_minute,
        embed_tokens_per_minute=embed_tokens_per_minute,
        chat_per_minute=chat_per_minute,
        summary_model=summary_model,
        vision_model=env.get("GEMINI_VISION_MODEL", "").strip() or summary_model,
    )


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip() or str(default)
    if not raw.isdigit() or int(raw) < 1:
        raise ConfigError(f"{name} must be a positive integer, got {raw!r}")
    return int(raw)

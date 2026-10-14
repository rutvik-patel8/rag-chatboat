"""Checks that the DB and Gemini are reachable and agree with .env.

Run from ingest/:  uv run python doctor.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from config import ConfigError, Settings, load_settings
from db import connect, embedding_column_dim
from gemini import make_embedder


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_database(settings: Settings, connect_fn=connect, dim_fn=embedding_column_dim) -> Check:
    try:
        with connect_fn(settings) as conn:
            dim = dim_fn(conn)
    except Exception as exc:
        return Check("database", False, f"Database check failed: {exc}")
    if dim != settings.embed_dim:
        return Check(
            "database",
            False,
            f"chunks.embedding is vector({dim}) but EMBED_DIM is {settings.embed_dim}; run db/reset.sh",
        )
    return Check("database", True, f"schema ok, chunks.embedding is vector({dim})")


def check_gemini(settings: Settings, embedder_factory=make_embedder) -> Check:
    try:
        [vector] = embedder_factory(settings).embed(["health check"], "RETRIEVAL_QUERY")
    except Exception as exc:
        return Check("gemini", False, f"Embedding call failed: {exc}")
    return Check("gemini", True, f"{settings.embed_model} returned {len(vector)} dims")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"✗ config: {exc}")
        return 1
    checks = [check_database(settings), check_gemini(settings)]
    for check in checks:
        print(f"{'✓' if check.ok else '✗'} {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())

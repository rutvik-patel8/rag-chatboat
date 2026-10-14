"""Postgres helpers. db/schema.sql is the contract shared with the Next.js app."""
from __future__ import annotations

import re

import psycopg
from pgvector.psycopg import register_vector

from config import Settings


def connect(settings: Settings) -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, connect_timeout=5)
    register_vector(conn)
    return conn


def embedding_column_dim(conn: psycopg.Connection) -> int:
    """Dimension declared for chunks.embedding in the live DB, e.g. 768 for vector(768)."""
    row = conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = to_regclass('public.chunks') AND attname = 'embedding'"
    ).fetchone()
    if row is None:
        raise RuntimeError("chunks.embedding not found; apply db/schema.sql with db/reset.sh")
    match = re.fullmatch(r"vector\((\d+)\)", row[0])
    if match is None:
        raise RuntimeError(f"chunks.embedding has unexpected type {row[0]}")
    return int(match.group(1))

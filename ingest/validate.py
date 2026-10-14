"""Post-ingest assertions (spec §9). Run from ingest/:  uv run python validate.py [--doc slug ...]"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Callable

import psycopg

from config import Settings, load_settings
from db import connect


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


# Per-document checks are added by the profile sub-projects (3: gpo, 4: gep, 5: imac).
DOC_CHECKS: dict[str, list[Callable[[psycopg.Connection], CheckResult]]] = {}


def generic_checks(conn: psycopg.Connection, settings: Settings, slug: str) -> list[CheckResult]:
    doc = conn.execute("SELECT id, embed_model, embed_dim FROM documents WHERE slug = %s", (slug,)).fetchone()
    if doc is None:
        return [CheckResult("document stored", False, f"no document '{slug}'; run ingest.py --doc {slug}")]
    doc_id, model, dim = doc

    def count(where: str, *params) -> int:
        return conn.execute(f"SELECT count(*) FROM chunks WHERE doc_id = %s AND {where}", (doc_id, *params)).fetchone()[0]

    total = count("true")
    empty = count("btrim(content) = ''")
    wrong_dim = count("vector_dims(embedding) <> %s", settings.embed_dim)
    orphans = count("section_id IS NULL")
    same_model = (model, dim) == (settings.embed_model, settings.embed_dim)
    return [
        CheckResult("document stored", True, f"{total} chunks"),
        CheckResult("has chunks", total > 0, f"{total} chunks"),
        CheckResult("no empty chunks", empty == 0, f"{empty} empty"),
        CheckResult("embedding dims", wrong_dim == 0, f"{wrong_dim} chunks not {settings.embed_dim}-dim"),
        CheckResult("chunks have sections", orphans == 0, f"{orphans} without a section"),
        CheckResult("embedding model", same_model, f"stored {model}/{dim}, .env {settings.embed_model}/{settings.embed_dim}"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check ingested documents.")
    parser.add_argument("--doc", dest="docs", action="append", help="slug to check (default: every stored document)")
    args = parser.parse_args(argv)
    settings = load_settings()
    ok = True
    with connect(settings) as conn:
        slugs = args.docs or [row[0] for row in conn.execute("SELECT slug FROM documents ORDER BY slug")]
        for slug in slugs:
            results = generic_checks(conn, settings, slug) + [check(conn) for check in DOC_CHECKS.get(slug, [])]
            for r in results:
                print(f"{'✓' if r.ok else '✗'} {slug}: {r.name} — {r.detail}")
            ok = ok and all(r.ok for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

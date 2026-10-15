"""Post-ingest assertions (spec §9). Run from ingest/:  uv run python validate.py [--doc slug ...]"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Callable

import psycopg

from config import Settings, load_settings
from db import connect
from profiles import DOCS


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


Check = Callable[[psycopg.Connection, str], CheckResult]


def chunk_with(needle: str, kind: str | None = None, path: str | None = None) -> Check:
    name = f"{kind or 'any'} chunk with {needle!r}" + (f" in {path}" if path else "")

    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id JOIN sections s ON s.id = c.section_id "
            "WHERE d.slug = %s AND strpos(c.content, %s) > 0 "
            "AND (%s::text IS NULL OR c.kind = %s) AND (%s::text IS NULL OR s.path = %s)",
            (slug, needle, kind, kind, path, path),
        ).fetchone()[0]
        return CheckResult(name, n > 0, f"{n} found")

    return check


def no_chunk_with(needle: str) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = %s AND strpos(c.content, %s) > 0",
            (slug, needle),
        ).fetchone()[0]
        return CheckResult(f"no chunk with {needle!r}", n == 0, f"{n} found")

    return check


def chunk_with_footnote(needle: str, n: int) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        count = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE d.slug = %s AND strpos(c.content, %s) > 0 AND c.footnotes @> %s::jsonb",
            (slug, needle, f'[{{"n": {n}}}]'),
        ).fetchone()[0]
        return CheckResult(f"chunk with {needle!r} citing footnote {n}", count > 0, f"{count} found")

    return check


def kind_at_least(kind: str, minimum: int) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = %s AND c.kind = %s",
            (slug, kind),
        ).fetchone()[0]
        return CheckResult(f"at least {minimum} {kind} chunks", n >= minimum, f"{n} found")

    return check


def section_exists(path: str) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM sections s JOIN documents d ON d.id = s.doc_id WHERE d.slug = %s AND s.path = %s",
            (slug, path),
        ).fetchone()[0]
        return CheckResult(f"section {path!r}", n > 0, f"{n} found")

    return check


def regions_within(count: int, first: int, last: int) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        rows = conn.execute(
            "SELECT s.title, s.page_start, s.page_end FROM sections s JOIN documents d ON d.id = s.doc_id "
            "WHERE d.slug = %s AND s.kind = 'region' ORDER BY s.page_start",
            (slug,),
        ).fetchall()
        inside = all(first <= a <= b <= last for _, a, b in rows)
        return CheckResult(f"{count} region sections within p{first}–{last}", len(rows) == count and inside, f"{rows}")

    return check


def no_chunks_under(title: str) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id JOIN sections s ON s.id = c.section_id "
            "WHERE d.slug = %s AND s.title = %s",
            (slug, title),
        ).fetchone()[0]
        return CheckResult(f"no chunks under {title!r}", n == 0, f"{n} found")

    return check


MESSAGE = "A Message from the Acting Deputy Director"
GEP_SAR = "Ch. 2 Regional Outlooks › South Asia"

# Per-document checks (spec §9).
DOC_CHECKS: dict[str, list[Check]] = {
    "imac": [
        chunk_with("0.59W", "table"),
        chunk_with("45%", "table"),
        chunk_with("measured from the chart", "chart"),
        chunk_with_footnote("58 percent less energy than the requirement for ENERGY STAR", 18),
        chunk_with("recycled aluminum in the stand"),
        kind_at_least("footnote", 19),
        kind_at_least("callout", 3),
        kind_at_least("chart", 3),
    ],
    "gep": [
        regions_within(6, 81, 106),
        chunk_with("Growth in SAR is expected to soften to 6.3 percent in 2026", "text", f"{GEP_SAR} › Outlook"),
        chunk_with("TABLE 2.9 South Asia forecast summary", "table"),
        chunk_with("The global economy is facing another major shock", "text", "Executive Summary"),
        chunk_with("Summary of Ch. 3 A Rising Challenge", "section_summary"),
        section_exists("Ch. 1 Global Outlook › BOX 1.1 How much will AI affect global growth?"),
        kind_at_least("chart", 40),
        kind_at_least("table", 100),
        kind_at_least("section_summary", 30),
        no_chunks_under("References"),
    ],
    "gpo": [
        chunk_with("visited 166 depository libraries", "text"),
        chunk_with("full-scale deployment of govinfo", "text", MESSAGE),
        chunk_with("procured more than $375 million", "text"),
        chunk_with("$375.7 million", "table", "GPO By the Numbers FY 2018"),
        section_exists("GPO and Open, Transparent Government › govinfo"),
        chunk_with("270,596", "text", "SECTION IV FINANCIAL STATEMENTS › 15. Major Customers"),
        no_chunk_with("Financial Year In Review"),
        kind_at_least("caption", 15),
        kind_at_least("table", 4),
    ],
}


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


def slugs_to_check(requested: list[str] | None) -> list[str]:
    """Every known document by default, so one that was never ingested fails instead of being skipped."""
    return requested or list(DOCS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check ingested documents.")
    parser.add_argument("--doc", dest="docs", action="append", help="slug to check (default: gpo, gep, imac)")
    args = parser.parse_args(argv)
    settings = load_settings()
    ok = True
    with connect(settings) as conn:
        for slug in slugs_to_check(args.docs):
            results = generic_checks(conn, settings, slug) + [check(conn, slug) for check in DOC_CHECKS.get(slug, [])]
            for r in results:
                print(f"{'✓' if r.ok else '✗'} {slug}: {r.name} — {r.detail}")
            ok = ok and all(r.ok for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

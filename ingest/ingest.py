"""Ingest the source PDFs into Postgres.

Run from ingest/:  uv run python ingest.py [--doc gpo|gep|imac ...] [--reset]
"""
from __future__ import annotations

import argparse
import sys

from cache import CACHE_ROOT, JsonCache
from config import load_settings
from db import connect
from enrich.summaries import summary_chunks
from gemini import make_embedder, make_generator
from pipeline import run
from profiles import DOCS, PROFILES


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest the source PDFs into Postgres.")
    parser.add_argument("--doc", dest="docs", action="append", choices=list(DOCS), help="document to ingest (repeatable; default: all)")
    parser.add_argument("--reset", action="store_true", help="drop this document's cached vision/summary/embedding results first")
    args = parser.parse_args(argv)
    args.docs = args.docs or list(DOCS)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    embedder = make_embedder(settings)
    generator = make_generator(settings, settings.summary_model)  # summaries can use a cheaper model's quota

    def summarizer(spec, sections, warnings):
        cache = JsonCache(CACHE_ROOT, f"{spec.slug}/summaries")
        return summary_chunks(spec.title, sections, generator, cache, settings.summary_model, warnings)

    with connect(settings) as conn:
        for slug in args.docs:
            spec = DOCS[slug]
            if args.reset:
                print(f"{slug}: cleared {JsonCache(CACHE_ROOT, slug).clear()} cache entries")
            report = run(spec, PROFILES[spec.profile], settings, conn, embedder, CACHE_ROOT, summarizer)
            print(report.format())
    return 0


if __name__ == "__main__":
    sys.exit(main())

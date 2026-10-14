"""Shared ingestion steps around a document profile (spec §5.1)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cache import JsonCache
from chunk.common import build_embed_text, count_tokens
from config import Settings
from embed import embed_texts
from model import ChunkDraft, Note, Page, SectionDraft
from parse.boilerplate import find_boilerplate, strip_boilerplate
from parse.footnotes import NoteIndex, split_notes, strip_markers
from parse.pdf_text import body_font_size, load_pages
from report import Report
from store import DocumentMeta, store_document

EMBED_TOKEN_LIMIT = 2000  # Gemini embedding input limit is 2048 tokens; cl100k is a close proxy


@dataclass(frozen=True)
class DocSpec:
    slug: str
    title: str
    path: Path
    profile: str
    skip_pages: frozenset[int] = frozenset()
    endnote_pages: frozenset[int] = frozenset()


@dataclass
class Prepared:
    pages: list[Page]
    body_size: float
    notes: NoteIndex
    page_count: int
    boilerplate: int


@dataclass
class BuildResult:
    sections: list[SectionDraft]
    chunks: list[ChunkDraft]
    warnings: list[str] = field(default_factory=list)


Profile = Callable[[DocSpec, Prepared], BuildResult]


def prepare(spec: DocSpec) -> Prepared:
    pages = load_pages(spec.path)
    body = body_font_size(pages)
    keys = find_boilerplate(pages)
    kept = [p for p in strip_boilerplate(pages, keys) if p.number not in spec.skip_pages]
    notes: list[Note] = []
    clean: list[Page] = []
    for page in kept:
        page_notes, rest = split_notes(page, body)
        notes += page_notes
        clean.append(strip_markers(rest))
    return Prepared(clean, body, NoteIndex(notes, spec.endnote_pages), len(pages), len(keys))


def section_for_page(sections: list[SectionDraft], page: int) -> SectionDraft:
    containing = [s for s in sections if s.page_start <= page <= s.page_end]
    return max(containing, key=lambda s: s.level) if containing else min(sections, key=lambda s: s.level)


def note_chunks(notes: list[Note], sections: list[SectionDraft]) -> list[ChunkDraft]:
    return [
        ChunkDraft(section_for_page(sections, note.page).key, "footnote", f"Footnote {note.n}: {note.text}", note.page, note.page)
        for note in notes
    ]


def attach_footnotes(chunks: list[ChunkDraft], index: NoteIndex, report: Report) -> dict[Note, str]:
    """Resolve each (page, n) ref on its own page; returns each cited note's first citing section."""
    unresolved = set(report.unresolved)
    cited: dict[Note, str] = {}
    for chunk in chunks:
        for page, n in chunk.refs:
            note = index.resolve(n, page)
            if note is None:
                unresolved.add((page, n))
                continue
            entry = {"n": n, "text": note.text}
            if entry not in chunk.footnotes:
                chunk.footnotes.append(entry)
            cited.setdefault(note, chunk.section_key)
    report.unresolved = sorted(unresolved)
    return cited


def check_token_limit(texts: list[str], chunks: list[ChunkDraft]) -> None:
    too_big = [f"p.{c.page_start} {c.kind} ({count_tokens(t)} tokens)" for t, c in zip(texts, chunks) if count_tokens(t) > EMBED_TOKEN_LIMIT]
    if too_big:
        raise ValueError(f"chunks over the {EMBED_TOKEN_LIMIT}-token embedding limit: {'; '.join(too_big)}")


def run(spec: DocSpec, profile: Profile, settings: Settings, conn, embedder, cache_root: Path) -> Report:
    report = Report(spec.slug, spec.title)
    prepared = prepare(spec)
    result = profile(spec, prepared)
    chunks = result.chunks + note_chunks(prepared.notes.notes, result.sections)
    attach_footnotes(chunks, prepared.notes, report)
    paths = {s.key: s.path for s in result.sections}
    texts = [build_embed_text(spec.title, paths[c.section_key], c.page_start, c.page_end, c.kind, c.content) for c in chunks]
    check_token_limit(texts, chunks)
    cache = JsonCache(cache_root, f"{spec.slug}/embeddings")
    vectors = embed_texts(texts, embedder, cache, settings.embed_model, settings.embed_dim)
    meta = DocumentMeta(spec.slug, spec.title, spec.profile, prepared.page_count)
    store_document(conn, meta, result.sections, chunks, texts, vectors, settings.embed_model, settings.embed_dim)
    report.pages = prepared.page_count
    report.sections = len(result.sections)
    report.notes = len(prepared.notes.notes)
    report.boilerplate = prepared.boilerplate
    report.chunks_by_kind = Counter(c.kind for c in chunks)
    report.warnings += result.warnings
    return report

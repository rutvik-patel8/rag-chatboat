"""Baseline profile: the whole document as one section, paragraphs packed into ~500-token chunks."""
from __future__ import annotations

from chunk.common import pack, paragraphs_from_pages
from model import ChunkDraft, SectionDraft
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 500
OVERLAP_TOKENS = 75


def build(spec: DocSpec, prepared: Prepared) -> BuildResult:
    numbers = [page.number for page in prepared.pages]
    root = SectionDraft("root", spec.title, 0, "", min(numbers), max(numbers))
    packed = pack(paragraphs_from_pages(prepared.pages), TARGET_TOKENS, OVERLAP_TOKENS)
    chunks = [ChunkDraft("root", "text", p.text, p.page_start, p.page_end, refs=p.refs) for p in packed]
    return BuildResult([root], chunks)

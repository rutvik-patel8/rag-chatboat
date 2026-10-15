"""Section summaries (spec §5.3): a short Gemini summary per major section, indexed as its own chunk.

Broad questions ("What does Chapter 3 say about sovereign debt?") match a summary better than any
single 350-token passage.
"""
from __future__ import annotations

from collections import defaultdict

from cache import JsonCache
from chunk.common import count_tokens, truncate_tokens
from model import ChunkDraft, SectionDraft

PROMPT_VERSION = "v1"
MIN_TOKENS = 300
MAX_INPUT_TOKENS = 6000
PROMPT = """You summarize one section of the report "{doc}".
Write 3-5 sentences using only facts stated in the text below. Keep key figures with their units and years.
Do not add outside knowledge or opinions.

Section: {path}

{text}"""


def subtree_texts(sections: list[SectionDraft]) -> dict[str, str]:
    """Each section's own text followed by its descendants' text, in document order."""
    children: dict[str, list[SectionDraft]] = defaultdict(list)
    for section in sections:
        if section.parent_key:
            children[section.parent_key].append(section)

    def text(section: SectionDraft) -> str:
        parts = [section.content or ""] + [text(child) for child in children[section.key]]
        return "\n\n".join(p for p in parts if p)

    return {section.key: text(section) for section in sections}


def summary_chunks(
    doc_title: str, sections: list[SectionDraft], generator, cache: JsonCache, model: str, warnings: list[str] | None = None
) -> list[ChunkDraft]:
    """After the first generation failure (e.g. a daily quota) no more calls are made; cached summaries still count."""
    texts = subtree_texts(sections)
    chunks = []
    failure: Exception | None = None
    skipped = 0
    for section in sections:
        text = texts[section.key]
        if not 1 <= section.level <= 2 or count_tokens(text) < MIN_TOKENS:
            continue
        prompt = PROMPT.format(doc=doc_title, path=section.path, text=truncate_tokens(text, MAX_INPUT_TOKENS))
        key = JsonCache.key(model, PROMPT_VERSION, prompt)
        summary = cache.get(key)
        if summary is None and failure is None:
            try:
                summary = generator.generate(prompt)
                cache.put(key, summary)
            except Exception as exc:
                failure = exc
        if summary is None:
            skipped += 1
            continue
        chunks.append(
            ChunkDraft(section.key, "section_summary", f"Summary of {section.path}: {summary}", section.page_start, section.page_end)
        )
    if skipped and warnings is not None:
        reason = str(failure).splitlines()[0][:120]
        warnings.append(f"{skipped} section summaries skipped ({reason}); re-run ingest.py later to fill them in")
    return chunks

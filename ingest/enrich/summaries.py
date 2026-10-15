"""Section summaries (spec §5.3): a short Gemini summary per major section, indexed as its own chunk.

Broad questions ("What does Chapter 3 say about sovereign debt?") match a summary better than any
single 350-token passage.
"""
from __future__ import annotations

import re
from collections import defaultdict

from cache import JsonCache
from chunk.common import count_tokens, truncate_tokens
from model import ChunkDraft, SectionDraft

PROMPT_VERSION = "v2"
MIN_TOKENS = 300
MAX_INPUT_TOKENS = 6000
PROMPT = """You summarize one section of the report "{doc}".
Write 3-5 sentences of plain prose using only facts stated in the text below. Keep key figures with their units
and years. Do not add outside knowledge or opinions. No preamble (start directly with the content), no bullet points,
no headings.

Section: {path}

{text}"""


_PREAMBLE_LINE = re.compile(r"^(?:here is|here's|below is)\b[^\n]*:\s*\n+", re.I)
_LEAD_IN = re.compile(r"^(?:based on|according to) the (?:provided |given )?(?:text|section)[^,]*,\s*", re.I)
_BULLET = re.compile(r"^\s*(?:[*\-•]|\d+\.)\s+", re.M)


def clean_summary(text: str) -> str:
    """Drop chatty preambles ("Here is a summary:") and flatten bullet lists into prose."""
    text = _PREAMBLE_LINE.sub("", text.strip())
    stripped = _LEAD_IN.sub("", text)
    if stripped != text:  # "Based on the text, debt rose" → "Debt rose"
        text = stripped[:1].upper() + stripped[1:]
    if _BULLET.search(text):
        text = " ".join(_BULLET.sub("", line).strip() for line in text.splitlines() if line.strip())
    return text.strip()


def _is_quota(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(exc)


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
    """A quota error stops further calls for this run; any other failure skips just that section. Cached summaries
    always count, so a rerun fills the gaps."""
    texts = subtree_texts(sections)
    chunks = []
    failure: Exception | None = None
    quota_hit = False
    skipped = 0
    for section in sections:
        text = texts[section.key]
        if not 1 <= section.level <= 2 or count_tokens(text) < MIN_TOKENS:
            continue
        prompt = PROMPT.format(doc=doc_title, path=section.path, text=truncate_tokens(text, MAX_INPUT_TOKENS))
        key = JsonCache.key(model, PROMPT_VERSION, prompt)
        summary = cache.get(key)
        if summary is None and not quota_hit:
            try:
                summary = clean_summary(generator.generate(prompt))
                cache.put(key, summary)
            except Exception as exc:
                failure = exc
                quota_hit = _is_quota(exc)
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

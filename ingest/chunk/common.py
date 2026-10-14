"""Shared chunking helpers: token counts, paragraphs from lines, token-bounded packing."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Callable

import tiktoken

from model import Page, Ref
from parse.pdf_text import join_lines, word_vocabulary


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for match in _SENTENCE_BREAK.finditer(text):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if a < b]


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    return [text[a:b] for a, b in _sentence_spans(text)]


@dataclass(frozen=True)
class Paragraph:
    text: str
    page: int
    refs: tuple[Ref, ...] = ()  # offsets into `text`


def paragraphs_from_pages(pages: list[Page], vocab: frozenset[str] | None = None) -> list[Paragraph]:
    """A new paragraph starts on a new block, a font-size change, or a vertical gap."""
    if vocab is None:
        vocab = word_vocabulary(pages)
    out: list[Paragraph] = []
    for page in pages:
        text, refs, previous = None, [], None
        for line in page.lines:
            if not line.text:
                continue
            starts_new = (
                previous is None
                or line.block != previous.block
                or abs(line.size - previous.size) > 0.5
                or line.bbox[1] - previous.bbox[3] > 0.8 * line.size
            )
            if starts_new:
                if text is not None:
                    out.append(Paragraph(text, page.number, tuple(refs)))
                text, refs = line.text, list(line.refs)
            else:
                text, start = join_lines(text, line.text, vocab)
                refs += [replace(r, offset=start + r.offset) for r in line.refs]
            previous = line
        if text is not None:
            out.append(Paragraph(text, page.number, tuple(refs)))
    return out


@dataclass(frozen=True)
class Packed:
    text: str
    page_start: int
    page_end: int
    refs: tuple[tuple[int, int], ...]  # (page, n)


@dataclass(frozen=True)
class _Unit:
    paragraph: int
    text: str
    page: int
    refs: tuple[Ref, ...]
    tokens: int


def _hard_split_spans(text: str, target: int, count: Callable[[str], int]) -> list[tuple[int, int]]:
    pieces: list[tuple[int, int]] = []
    first = last = None
    for word in re.finditer(r"\S+", text):
        if first is not None and count(text[first : word.end()]) > target:
            pieces.append((first, last))
            first = None
        if first is None:
            first = word.start()
        last = word.end()
    if first is not None:
        pieces.append((first, last))
    return pieces


def _units(paragraphs: list[Paragraph], target: int, count: Callable[[str], int]) -> list[_Unit]:
    """Paragraphs, or their sentences/word runs when too long. A marker goes only to the piece holding it."""
    units = []
    for index, paragraph in enumerate(paragraphs):
        text = paragraph.text
        tokens = count(text)
        if tokens <= target:
            units.append(_Unit(index, text, paragraph.page, paragraph.refs, tokens))
            continue
        for s_start, s_end in _sentence_spans(text):
            sentence = text[s_start:s_end]
            if count(sentence) <= target:
                pieces = [(s_start, s_end)]
            else:
                pieces = [(s_start + a, s_start + b) for a, b in _hard_split_spans(sentence, target, count)]
            for a, b in pieces:
                refs = tuple(r for r in paragraph.refs if a <= r.offset <= b)
                units.append(_Unit(index, text[a:b], paragraph.page, refs, count(text[a:b])))
    return units


def _join(units: list[_Unit]) -> Packed:
    text = units[0].text
    for previous, unit in zip(units, units[1:]):
        text += (" " if unit.paragraph == previous.paragraph else "\n\n") + unit.text
    refs: list[tuple[int, int]] = []
    for unit in units:
        refs += [(r.page, r.n) for r in unit.refs if (r.page, r.n) not in refs]
    return Packed(text, min(u.page for u in units), max(u.page for u in units), tuple(refs))


def pack(
    paragraphs: list[Paragraph], target_tokens: int, overlap_tokens: int, count: Callable[[str], int] = count_tokens
) -> list[Packed]:
    chunks: list[Packed] = []
    current: list[_Unit] = []
    current_tokens = 0
    for unit in _units(paragraphs, target_tokens, count):
        if current and current_tokens + unit.tokens > target_tokens:
            chunks.append(_join(current))
            tail: list[_Unit] = []
            tail_tokens = 0
            for previous in reversed(current):
                if tail_tokens + previous.tokens > overlap_tokens:
                    break
                tail.insert(0, previous)
                tail_tokens += previous.tokens
            if len(tail) == len(current) or tail_tokens + unit.tokens > target_tokens:
                tail, tail_tokens = [], 0
            current, current_tokens = tail, tail_tokens
        current.append(unit)
        current_tokens += unit.tokens
    if current:
        chunks.append(_join(current))
    return chunks


def build_embed_text(doc_title: str, section_path: str, page_start: int, page_end: int, kind: str, content: str) -> str:
    pages = f"p.{page_start}" if page_start == page_end else f"p.{page_start}-{page_end}"
    where = f"{doc_title} › {section_path}" if section_path else doc_title
    return f"{where} | {pages} | {kind}\n\n{content}"

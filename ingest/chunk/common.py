"""Shared chunking helpers: token counts, paragraphs from lines, token-bounded packing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

import tiktoken

from model import Page


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_BREAK.split(text.strip()) if s]


@dataclass(frozen=True)
class Paragraph:
    text: str
    page: int
    refs: tuple[int, ...] = ()


def paragraphs_from_pages(pages: list[Page]) -> list[Paragraph]:
    """A new paragraph starts on a new block, a font-size change, or a vertical gap."""
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
                if text.endswith("-") and line.text[:1].islower():
                    text = text[:-1] + line.text
                else:
                    text = f"{text} {line.text}"
                refs += [r for r in line.refs if r not in refs]
            previous = line
        if text is not None:
            out.append(Paragraph(text, page.number, tuple(refs)))
    return out


@dataclass(frozen=True)
class Packed:
    text: str
    page_start: int
    page_end: int
    refs: tuple[int, ...]


@dataclass(frozen=True)
class _Unit:
    paragraph: int
    text: str
    page: int
    refs: tuple[int, ...]
    tokens: int


def _hard_split(text: str, target: int, count: Callable[[str], int]) -> list[str]:
    pieces, current = [], []
    for word in text.split():
        if current and count(" ".join(current + [word])) > target:
            pieces.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _units(paragraphs: list[Paragraph], target: int, count: Callable[[str], int]) -> list[_Unit]:
    units = []
    for index, paragraph in enumerate(paragraphs):
        tokens = count(paragraph.text)
        if tokens <= target:
            units.append(_Unit(index, paragraph.text, paragraph.page, paragraph.refs, tokens))
            continue
        for sentence in split_sentences(paragraph.text):
            pieces = [sentence] if count(sentence) <= target else _hard_split(sentence, target, count)
            for piece in pieces:
                units.append(_Unit(index, piece, paragraph.page, paragraph.refs, count(piece)))
    return units


def _join(units: list[_Unit]) -> Packed:
    text = units[0].text
    for previous, unit in zip(units, units[1:]):
        text += (" " if unit.paragraph == previous.paragraph else "\n\n") + unit.text
    refs: list[int] = []
    for unit in units:
        refs += [r for r in unit.refs if r not in refs]
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

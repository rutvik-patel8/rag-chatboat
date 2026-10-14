"""Footnotes: note text (page bottom or endnote pages) and the superscript markers that cite them."""
from __future__ import annotations

import re
from dataclasses import replace

from model import Line, Note, Page

_NUMBER = re.compile(r"\d{1,3}")
_MARKER = re.compile(r"\s*\d{1,3}(?:\s*,\s*\d{1,3})*\s*")


def note_number(line: Line) -> int | None:
    """A note line starts with a small number followed by larger text, e.g. '18 Energy consumption…'."""
    spans = [span for span in line.spans if span.text.strip()]
    if len(spans) < 2:
        return None
    number, text = spans[0], spans[1]
    if _NUMBER.fullmatch(number.text.strip()) and number.size < 0.8 * text.size:
        return int(number.text.strip())
    return None


def split_notes(page: Page, body_size: float) -> tuple[list[Note], Page]:
    found: list[dict] = []
    keep: list[Line] = []
    current: dict | None = None
    for line in page.lines:
        n = note_number(line)
        if n is not None and line.size < body_size:
            spans = [span for span in line.spans if span.text.strip()][1:]
            current = {
                "n": n,
                "parts": [re.sub(r"\s+", " ", "".join(span.text for span in spans)).strip()],
                "size": line.size,
                "bottom": line.bbox[3],
            }
            found.append(current)
            continue
        gap = line.bbox[1] - current["bottom"] if current else None
        if current and abs(line.size - current["size"]) < 0.3 and -current["size"] <= gap < 1.5 * current["size"]:
            current["parts"].append(line.text)
            current["bottom"] = line.bbox[3]
            continue
        if line.size >= body_size:
            current = None
        keep.append(line)
    notes = [Note(item["n"], page.number, " ".join(item["parts"])) for item in found]
    return notes, replace(page, lines=tuple(keep))


def strip_markers(page: Page) -> Page:
    lines: list[Line] = []
    for line in page.lines:
        refs: list[int] = []
        spans = []
        for span in line.spans:
            if span.is_superscript and _MARKER.fullmatch(span.text) and span.size < 0.85 * line.size:
                refs.extend(int(n) for n in re.findall(r"\d+", span.text))
            else:
                spans.append(span)
        lines.append(replace(line, spans=tuple(spans), refs=tuple(refs)) if refs else line)
    return replace(page, lines=tuple(lines))


class NoteIndex:
    def __init__(self, notes: list[Note], endnote_pages: frozenset[int] = frozenset()):
        self.notes = list(notes)
        self.endnote_pages = endnote_pages

    def resolve(self, n: int, page: int) -> Note | None:
        """Same page first, then the endnote pages. Never guess from other pages: numbering restarts."""
        for note in self.notes:
            if note.n == n and note.page == page:
                return note
        for note in self.notes:
            if note.n == n and note.page in self.endnote_pages:
                return note
        return None

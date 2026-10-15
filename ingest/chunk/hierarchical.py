"""Hierarchical profile for the World Bank Global Economic Prospects report (spec §5.3)."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import pymupdf

from chunk.common import count_tokens, pack, paragraphs_from_pages
from model import ChunkDraft, Line, Page, SectionDraft, Span
from parse.tables import merge_symbol_cells, pad_numeric_rows, region_rows, split_table
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 350
OVERLAP_TOKENS = 50
TABLE_TOKENS = 400
_OBJECT = re.compile(r"(FIGURE|TABLE|BOX)\s*(B?\d+(?:\.\d+)+)")
_PANEL = re.compile(r"[A-F]\.\s")


@dataclass(frozen=True)
class GepLayout:
    """Typography observed in Global Economic Prospects, June 2026."""

    front_title: tuple[str, float] = ("HelveticaLTStd-Cond", 26.0)  # Acknowledgments, Foreword, Executive Summary…
    section_font: str = "HelveticaLTStd-Bold"
    section_sizes: tuple[float, ...] = (16.0, 14.0)  # "Summary", "South Asia", "ANNEX 3.1 …"; Ch. 2 "References" is 14pt
    subsection: tuple[str, float, int] = ("HelveticaLTStd-Roman", 11.0, 0x4475A1)  # "Outlook", box sub-heads
    minor: tuple[str, float, int] = ("AGaramondPro-BoldItalic", 11.0, 0x4475A1)  # "Downside risks"
    object_title: tuple[str, float] = ("HelveticaLTStd-Bold", 10.0)  # FIGURE / TABLE / BOX x.y titles
    box_body_size: float = 10.6  # box text; chapter text is 11pt
    chart_text: frozenset[tuple[str, float]] = frozenset(
        {("HelveticaLTStd-Obl", 8.0), ("HelveticaLTStd-Bold", 7.0), ("HelveticaLTStd-Roman", 6.0)}
    )
    note_text: tuple[str, float] = ("HelveticaLTStd-Roman", 6.0)  # Sources / Note lines under figures and tables
    annex_table_pages: frozenset[int] = frozenset({187, 188, 189, 190})
    regions: tuple[str, ...] = (
        "East Asia and Pacific",
        "Europe and Central Asia",
        "Latin America and the Caribbean",
        "Middle East, North Africa, Afghanistan, and Pakistan",
        "South Asia",
        "Sub-Saharan Africa",
    )
    excluded_titles: frozenset[str] = frozenset({"References"})


GEP_LAYOUT = GepLayout()


def _main(line: Line) -> Span | None:
    spans = [s for s in line.spans if s.text.strip()]
    return max(spans, key=lambda s: len(s.text.strip())) if spans else None


def _is(span: Span | None, font: str, size: float, color: int | None = None) -> bool:
    return span is not None and span.font == font and round(span.size, 1) == size and (color is None or span.color == color)


def line_role(line: Line, layout: GepLayout) -> str:
    """front | section | subsection | minor | object | graphic | box_text | text"""
    m = _main(line)
    if m is None:
        return "text"
    if _is(m, *layout.front_title):
        return "front"
    if m.font == layout.section_font and round(m.size, 1) in layout.section_sizes:
        return "section"
    if _is(m, *layout.subsection):
        return "subsection"
    if _is(m, *layout.minor):
        return "minor"
    if _is(m, *layout.object_title) and _OBJECT.match(line.text):
        return "object"
    if m.font.startswith("Helvetica"):
        return "graphic"
    if abs(m.size - layout.box_body_size) < 0.05:
        return "box_text"
    return "text"


def chapter_starts(pdf_path: Path) -> dict[int, str]:
    """Chapter-level bookmarks ("Ch. 1 Global Outlook", "Statistical Annex") by start page."""
    with pymupdf.open(pdf_path) as doc:
        toc = doc.get_toc()
    starts = {}
    for _, title, page in toc:
        title = re.sub(r"\s+", " ", title).strip()
        if title.startswith("Ch. ") or title == "Statistical Annex":
            starts[page] = title
    return starts


def figure_text(title: str, lines: list[Line], layout: GepLayout) -> str:
    """Title, the italic takeaway, panel titles and sources; chart tick labels are dropped."""
    summary, panels, notes = [], [], []
    for line in lines:
        m = _main(line)
        key = (m.font, round(m.size, 1)) if m else None
        if key == ("HelveticaLTStd-Obl", 8.0):
            summary.append(line.text)
        elif key == ("HelveticaLTStd-Bold", 7.0):
            if _PANEL.match(line.text) or not panels:
                panels.append(line.text)
            else:
                panels[-1] += " " + line.text
        elif key == layout.note_text:
            notes.append(line.text)
    parts = [title, " ".join(summary)]
    if panels:
        parts.append("Panels: " + "; ".join(panels))
    parts.append(" ".join(notes))
    return "\n\n".join(p for p in parts if p)


def table_pieces(title: str, lines: list[Line], page: Page, layout: GepLayout) -> tuple[list[str], str]:
    cells = [l for l in lines if (_main(l).font, round(_main(l).size, 1)) != layout.note_text]
    notes = " ".join(l.text for l in lines if (_main(l).font, round(_main(l).size, 1)) == layout.note_text)
    rows = pad_numeric_rows(merge_symbol_cells(region_rows(replace(page, lines=tuple(cells)), (0, 0, page.width, page.height))))
    if rows and rows[0] and rows[0][0].replace(" ", "").startswith(title.replace(" ", "")[:12]):
        rows = rows[1:]  # the title itself was captured as the first row
    header_rows = next((i + 1 for i, row in enumerate(rows) if "2026f" in row), 1)
    budget = TABLE_TOKENS - count_tokens(title) - 10
    pieces = [f"{title}\n\n{md}" for md in split_table(rows, header_rows, budget, count_tokens)]
    return pieces, (f"{title}\n\n{notes}" if notes else "")

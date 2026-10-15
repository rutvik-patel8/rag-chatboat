"""Rebuild table rows from span positions inside a region chosen by the profile.

PyMuPDF's find_tables() was rejected after testing on these PDFs (spec §5.1 step 3).
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Callable

from model import BBox, Page, Span


def _center(bbox: BBox) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def _inside(bbox: BBox, region: BBox) -> bool:
    x, y = _center(bbox)
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def region_rows(page: Page, bbox: BBox, row_tolerance: float = 2.0, cell_gap: float = 6.0) -> list[list[str]]:
    spans = [s for line in page.lines for s in line.spans if s.text.strip() and _inside(s.bbox, bbox)]
    spans.sort(key=lambda s: (_center(s.bbox)[1], s.bbox[0]))
    rows: list[list[Span]] = []
    row_y: float | None = None
    for span in spans:
        y = _center(span.bbox)[1]
        if row_y is not None and abs(y - row_y) <= row_tolerance:
            rows[-1].append(span)
        else:
            rows.append([span])
            row_y = y
    return [_cells(row, cell_gap) for row in rows]


def _cells(row: list[Span], cell_gap: float) -> list[str]:
    cells: list[str] = []
    previous: Span | None = None
    for span in sorted(row, key=lambda s: s.bbox[0]):
        text = span.text.strip()
        gap = span.bbox[0] - previous.bbox[2] if previous else None
        if previous is not None and gap <= cell_gap:
            touching = gap <= 0.5 and span.size == previous.size and span.flags == previous.flags
            cells[-1] += ("" if touching else " ") + text
        else:
            cells.append(text)
        previous = span
    return [re.sub(r"\s+", " ", cell) for cell in cells]


def rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]

    def line(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join([line(padded[0]), "|" + "---|" * width] + [line(row) for row in padded[1:]])


def split_table(rows: list[list[str]], header_rows: int, max_tokens: int, count: Callable[[str], int]) -> list[str]:
    header, body = rows[:header_rows], rows[header_rows:]
    groups: list[list[list[str]]] = []
    current: list[list[str]] = []
    for row in body:
        if current and count(rows_to_markdown(header + current + [row])) > max_tokens:
            groups.append(current)
            current = []
        current.append(row)
    if current or not groups:
        groups.append(current)
    return [rows_to_markdown(header + group) for group in groups]


def remove_region(page: Page, bbox: BBox) -> Page:
    return replace(page, lines=tuple(line for line in page.lines if not _inside(line.bbox, bbox)))


def merge_symbol_cells(rows: list[list[str]], symbols: tuple[str, ...] = ("$",)) -> list[list[str]]:
    """Join a lone currency-symbol cell to the value after it: ["$", "644,807"] → ["$644,807"]."""
    merged = []
    for row in rows:
        out: list[str] = []
        for cell in row:
            if out and out[-1] in symbols:
                out[-1] += cell
            else:
                out.append(cell)
        merged.append(out)
    return merged


_NUMERIC_CELL = re.compile(r"[\d$,.()%/-]+(?:[A-Za-z]+\d*)*")  # 2018, $644,807, 2026f, 24Q4, 26Q1e, 2025/26e


def pad_numeric_rows(rows: list[list[str]]) -> list[list[str]]:
    """A short row of only numbers (a "2018 | 2017" header) belongs over the right-hand value columns."""
    width = max((len(row) for row in rows), default=0)
    return [
        [""] * (width - len(row)) + row if len(row) < width and all(_NUMERIC_CELL.fullmatch(c) for c in row) else row
        for row in rows
    ]

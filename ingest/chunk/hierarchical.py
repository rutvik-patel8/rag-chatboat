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
_OBJECT = re.compile(r"(FIGURE|TABLE|BOX)\s*([AB]?\d+(?:\.\d+)+)")  # 1.1, B1.1.1 (box), A3.1 (annex)
_PANEL = re.compile(r"[A-F]\.\s")


@dataclass(frozen=True)
class GepLayout:
    """Typography observed in Global Economic Prospects, June 2026."""

    front_title: tuple[str, float] = ("HelveticaLTStd-Cond", 26.0)  # Acknowledgments, Foreword, Executive Summary…
    section_font: str = "HelveticaLTStd-Bold"
    section_sizes: tuple[float, ...] = (16.0, 14.0)  # "Summary", "South Asia", "ANNEX 3.1 …"; Ch. 2 "References" is 14pt
    subsection: tuple[str, float] = ("HelveticaLTStd-Roman", 11.0)  # any blue: "Outlook", box and Ch. 3 sub-heads
    minor: tuple[str, float, int] = ("AGaramondPro-BoldItalic", 11.0, 0x4475A1)  # "Downside risks"
    object_font: str = "HelveticaLTStd-Bold"  # FIGURE / TABLE / BOX x.y titles (TABLE 4.1 is 11pt)
    object_sizes: tuple[float, ...] = (10.0, 11.0)
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
    if _is(m, *layout.subsection) and m.color != 0:  # several blues are used; black 11pt Helvetica is chart text
        return "subsection"
    if _is(m, *layout.minor):
        return "minor"
    if m.font == layout.object_font and round(m.size, 1) in layout.object_sizes and _OBJECT.match(line.text):
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


def reading_order(page: Page) -> Page:
    """Left column, then right column, each top to bottom. PDF stream order often starts with the right
    column or a heading block. Lines wider than 55% of the page (single-column pages, titles) count as left."""

    def column(line: Line) -> int:
        x0, _, x1, _ = line.bbox
        return 0 if x1 - x0 > 0.55 * page.width or (x0 + x1) / 2 < page.width / 2 else 1

    return replace(page, lines=tuple(sorted(page.lines, key=lambda l: (column(l), round(l.bbox[1], 1), l.bbox[0]))))


class _Outline:
    """The section tree as headings arrive in reading order, plus the body lines filed under it."""

    def __init__(self, root: SectionDraft, layout: GepLayout):
        self.layout = layout
        self.root = root
        self.sections: list[SectionDraft] = [root]
        self.by_key = {"root": root}
        self.body: dict[str, list[tuple[int, Line]]] = defaultdict(list)
        self.boxes: dict[str, SectionDraft] = {}
        self.l1, self.l2, self.l3, self.l4 = root, None, None, None
        self.box: SectionDraft | None = None
        self.box_leaf: SectionDraft | None = None
        self.last: tuple[SectionDraft, Line, str] | None = None  # heading just opened, until body text follows

    def add(self, level: int, title: str, page_no: int, parent: SectionDraft, kind: str = "section") -> SectionDraft:
        path = title if parent is self.root else f"{parent.path} › {title}"
        section = SectionDraft(f"s{len(self.sections)}", title, level, path, page_no, page_no, parent.key, kind)
        self.sections.append(section)
        self.by_key[section.key] = section
        return section

    def chapter_leaf(self) -> SectionDraft:
        return self.l4 or self.l3 or self.l2 or self.l1

    def open_l1(self, title: str, page_no: int, kind: str) -> None:
        self.l1, self.l2, self.l3, self.l4 = self.add(1, title, page_no, self.root, kind), None, None, None
        self.box = self.box_leaf = None
        self.last = None

    def _continues(self, line: Line, role: str) -> bool:
        """A wrapped heading: same role, directly below the line just opened."""
        return self.last is not None and self.last[2] == role and 0 <= line.bbox[1] - self.last[1].bbox[3] < 0.6 * line.size

    def _extend(self, line: Line, role: str) -> None:
        section = self.last[0]
        section.title = f"{section.title} {line.text}"
        parent = self.by_key[section.parent_key]
        section.path = section.title if parent is self.root else f"{parent.path} › {section.title}"
        if section.title in self.layout.regions:
            section.kind = "region"
        self.last = (section, line, role)

    def section_heading(self, line: Line, page_no: int) -> None:
        title = line.text
        if self._continues(line, "section") and not title.startswith("ANNEX "):
            return self._extend(line, "section")
        if self.l2 is not None and title.endswith(" (continued)") and self.l2.title == title.removesuffix(" (continued)"):
            self.last = (self.l2, line, "section")
            return
        kind = "region" if title in self.layout.regions else "annex" if title.startswith("ANNEX ") else "section"
        self.l2, self.l3, self.l4 = self.add(2, title, page_no, self.l1, kind), None, None
        self.box = self.box_leaf = None
        self.last = (self.l2, line, "section")

    def box_title(self, line: Line, number: str, page_no: int) -> None:
        box = self.boxes.get(number)
        if box is None:
            box = self.boxes[number] = self.add(2, line.text, page_no, self.l1, "box")
        if self.box is not box:  # later pages of a box repeat its title as a running header
            self.box = self.box_leaf = box
        self.last = None

    def sub_heading(self, line: Line, role: str, page_no: int, next_body_role: str | None) -> None:
        if self._continues(line, role):
            return self._extend(line, role)
        if role == "subsection" and self.box is not None and next_body_role == "box_text":
            self.box_leaf = opened = self.add(3, line.text, page_no, self.box)
        elif role == "subsection":
            parent = self.l2 or self.l1
            self.l3, self.l4 = self.add(parent.level + 1, line.text, page_no, parent), None
            opened = self.l3
        else:
            parent = self.l3 or self.l2 or self.l1
            self.l4 = opened = self.add(parent.level + 1, line.text, page_no, parent)
        self.last = (opened, line, role)

    def body_line(self, line: Line, role: str, page_no: int) -> None:
        target = self.box_leaf if role == "box_text" and self.box is not None else self.chapter_leaf()
        self.body[target.key].append((page_no, line))
        self.last = None


def build(spec: DocSpec, prepared: Prepared, layout: GepLayout = GEP_LAYOUT) -> BuildResult:
    chapters = chapter_starts(spec.path)
    pages = [reading_order(page) for page in prepared.pages]
    outline = _Outline(SectionDraft("root", spec.title, 0, "", pages[0].number, pages[-1].number), layout)
    chunks: list[ChunkDraft] = []

    for page in pages:
        if page.number in chapters:
            outline.open_l1(chapters[page.number], page.number, "annex" if chapters[page.number] == "Statistical Annex" else "section")
        objects: list[tuple[float, str, SectionDraft]] = []  # FIGURE/TABLE titles on this page: (y, title, section)
        graphics: list[Line] = []
        roles = [line_role(line, layout) for line in page.lines]
        for i, (line, role) in enumerate(zip(page.lines, roles)):
            if role == "front":
                outline.open_l1(line.text, page.number, "front")
            elif role == "section":
                outline.section_heading(line, page.number)
            elif role == "object":
                label, number = _OBJECT.match(line.text).groups()
                if label == "BOX":
                    outline.box_title(line, number, page.number)
                else:
                    in_box = number.startswith("B") and outline.box is not None
                    objects.append((line.bbox[1], line.text, outline.box if in_box else outline.chapter_leaf()))
            elif role in ("subsection", "minor"):
                next_body = next((r for r in roles[i + 1 :] if r in ("text", "box_text")), None)
                outline.sub_heading(line, role, page.number, next_body)
            elif role == "graphic":
                graphics.append(line)
            else:
                outline.body_line(line, role, page.number)

        def emit_table(title: str, members: list[Line], target: SectionDraft) -> None:
            pieces, notes = table_pieces(title, members, page, layout)
            chunks.extend(ChunkDraft(target.key, "table", piece, page.number, page.number) for piece in pieces)
            if notes:
                chunks.append(ChunkDraft(target.key, "caption", notes, page.number, page.number))

        if page.number in layout.annex_table_pages and outline.l2 is not None:
            emit_table(outline.l2.title, graphics, outline.l2)
            continue
        objects.sort(key=lambda o: o[0])
        for idx, (y, title, target) in enumerate(objects):  # each graphic line belongs to the title above it
            end = objects[idx + 1][0] if idx + 1 < len(objects) else float("inf")
            members = [l for l in graphics if y - 1 <= l.bbox[1] < end - 1 and l.text != title]
            if title.startswith("FIGURE"):
                chunks.append(ChunkDraft(target.key, "chart", figure_text(title, members, layout), page.number, page.number))
            else:
                emit_table(title, members, target)

    vocab = prepared.vocab or None
    for section in outline.sections:
        if section.title in layout.excluded_titles:  # bibliographies would swamp keyword search with author names
            continue
        grouped: dict[int, list[Line]] = defaultdict(list)
        for page_no, line in outline.body[section.key]:
            grouped[page_no].append(line)
        pseudo = [Page(n, pages[0].width, pages[0].height, tuple(ls)) for n, ls in sorted(grouped.items())]
        paragraphs = paragraphs_from_pages(pseudo, vocab)
        section.content = "\n\n".join(p.text for p in paragraphs) or None
        for packed in pack(paragraphs, TARGET_TOKENS, OVERLAP_TOKENS):
            chunks.append(ChunkDraft(section.key, "text", packed.text, packed.page_start, packed.page_end, refs=packed.refs))
    for chunk in chunks:  # a section spans its own and its descendants' pages
        section = outline.by_key[chunk.section_key]
        while section is not outline.root:
            section.page_start = min(section.page_start, chunk.page_start)
            section.page_end = max(section.page_end, chunk.page_end)
            section = outline.by_key[section.parent_key]
    return BuildResult(outline.sections, chunks)

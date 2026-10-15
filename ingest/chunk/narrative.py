"""Narrative profile for the GPO annual report (spec §5.2).

Headings are found by font style, not size: the narrative pages use one body face and mark
section titles with a medium-weight sans in a fixed size and colour; the financial pages use
bold headings that sit alone on their row. Each heading starts a section; chunks never cross one.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable

from chunk.common import count_tokens, pack, paragraphs_from_pages
from model import ChunkDraft, Line, Page, SectionDraft, Span
from parse.boilerplate import find_boilerplate, in_margin, strip_boilerplate
from parse.tables import merge_symbol_cells, pad_numeric_rows, region_rows, split_table
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 500
OVERLAP_TOKENS = 75
TABLE_TOKENS = 450

_BOLD = re.compile(r"Bold", re.I)
# Faces of the real financial headings; the OIG memo (Cambria/Calibri Bold, p51–52) uses others for its labels.
_FINANCIAL_HEADING_FACES = ("Arial-Bold", "TimesNewRomanPS-Bold")
_NUMERIC = re.compile(r"[\d$,.()\s%-]+")


@dataclass(frozen=True)
class Style:
    font: str
    size: float
    color: int

    def matches(self, span: Span) -> bool:
        return span.font == self.font and round(span.size, 1) == self.size and span.color == self.color


@dataclass(frozen=True)
class NarrativeLayout:
    """Styles and pages observed in the GPO 2018 Annual Report."""

    narrative_last_page: int = 30  # pages 31–82 are the audited financial statements
    level1: Style = Style("HelveticaNeueLTStd-Md", 10.0, 0x646E74)  # grey section titles
    level2: Style = Style("HelveticaNeueLTStd-Md", 9.5, 0x507CBF)  # blue sub-headings, often run-in
    caption: tuple[str, float] = ("HelveticaNeueLTStd-Roman", 8.2)
    injected: dict[int, str] = field(
        default_factory=lambda: {2: "Letter of Transmittal", 7: "About GPO", 28: "Organizational Chart"}
    )
    # Pages outside the reading flow (the infographic); consecutive pages with one title share a section.
    standalone_pages: dict[int, str] = field(
        default_factory=lambda: {14: "GPO By the Numbers FY 2018", 15: "GPO By the Numbers FY 2018"}
    )
    table_pages: frozenset[int] = frozenset({14, 15, 58, 59, 60})
    # The financial pages' footer and page number sit ~9–11% from the bottom, inside the shared 8% band's blind spot.
    footer_margin: float = 0.12


GPO_LAYOUT = NarrativeLayout()


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    rest: Line | None = None  # body text after a run-in heading on the same line


def _visible(line: Line) -> list[Span]:
    return [span for span in line.spans if span.text.strip()]


def narrative_heading(line: Line, layout: NarrativeLayout) -> Heading | None:
    spans = _visible(line)
    if not spans:
        return None
    if all(layout.level1.matches(s) for s in spans):
        return Heading(1, line.text)
    if not layout.level2.matches(spans[0]):
        return None
    head: list[Span] = []
    for span in line.spans:
        if span.text.strip() and not layout.level2.matches(span):
            break
        head.append(span)
    tail = line.spans[len(head):]
    title = re.sub(r"\s+", " ", "".join(s.text for s in head)).strip()
    rest = None
    if any(s.text.strip() for s in tail):
        rest = replace(line, spans=tuple(tail), bbox=(tail[0].bbox[0], *line.bbox[1:]), refs=())
    return Heading(2, title, rest)


def is_bold(line: Line) -> bool:
    spans = _visible(line)
    return bool(spans) and all(_BOLD.search(s.font) for s in spans)


def standalone(line: Line, page: Page) -> bool:
    """No other line shares this line's row (rules out bold table labels and memo fields)."""
    top, bottom = line.bbox[1], line.bbox[3]
    for other in page.lines:
        if other is line:
            continue
        overlap = min(bottom, other.bbox[3]) - max(top, other.bbox[1])
        if overlap > 0.5 * min(bottom - top, other.bbox[3] - other.bbox[1]):
            return False
    return True


def _financial_candidate(line: Line, page: Page) -> bool:
    return (
        all(s.font.startswith(_FINANCIAL_HEADING_FACES) for s in _visible(line))
        and is_bold(line)
        and 11.5 <= line.size <= 14.5
        and len(line.text) < 90
        and not _NUMERIC.fullmatch(line.text)
        and not line.text.endswith(":")  # "Restricted:" labels a table block, not a section
        and standalone(line, page)
    )


def repeated_titles(pages: list[Page], layout: NarrativeLayout) -> set[str]:
    """Bold title-block lines printed on many pages ("U.S. Government Publishing Office")."""
    counts: dict[str, int] = defaultdict(int)
    for page in pages:
        if page.number > layout.narrative_last_page:
            for line in page.lines:
                if _financial_candidate(line, page):
                    counts[line.text] += 1
    return {title for title, n in counts.items() if n >= 3}


def financial_heading(line: Line, page: Page, repeated: set[str]) -> Heading | None:
    if line.size >= 20:  # section divider pages: "SECTION I", "MANAGEMENT DISCUSSION AND ANALYSIS"
        return Heading(1, line.text)
    if _financial_candidate(line, page) and line.text not in repeated:
        return Heading(2, line.text)
    return None


def is_caption(line: Line, layout: NarrativeLayout) -> bool:
    spans = _visible(line)
    if not spans:
        return False
    main = max(spans, key=lambda s: len(s.text.strip()))
    return (main.font, round(main.size, 1)) == layout.caption


def merge_drop_caps(page: Page, body_size: float) -> Page:
    """A one-letter decorative initial ("T" + "he U.S. Government…") rejoins the line it starts."""
    lines: list[Line | None] = list(page.lines)
    for i, line in enumerate(lines):
        if line is not None and len(line.text) == 1 and line.text.isupper() and line.size >= 3 * body_size:
            for j in range(i + 1, len(lines)):
                if lines[j] is not None and lines[j].text[:1].islower():
                    lines[j] = replace(lines[j], spans=line.spans + lines[j].spans)
                    lines[i] = None
                    break
    return replace(page, lines=tuple(l for l in lines if l is not None))


def hoist_page_title(page: Page, is_title: Callable[[Line], bool]) -> Page:
    """PDF order can put a page-top title after the body below it; move it to the front."""
    others = [l for l in page.lines if not is_title(l)]
    if not others:
        return page
    top = min(l.bbox[1] for l in others)
    titles = [l for l in page.lines if is_title(l) and l.bbox[1] <= top]
    rest = [l for l in page.lines if not any(l is t for t in titles)]
    return replace(page, lines=tuple(titles + rest))


def top_to_bottom(page: Page) -> Page:
    """Single-column pages read top to bottom; PDF order can put a table after the next heading."""
    return replace(page, lines=tuple(sorted(page.lines, key=lambda l: (round(l.bbox[1]), l.bbox[0]))))


def build(spec: DocSpec, prepared: Prepared, layout: NarrativeLayout = GPO_LAYOUT) -> BuildResult:
    pages = [merge_drop_caps(p, prepared.body_size) for p in prepared.pages]
    footers = find_boilerplate(pages, margin=layout.footer_margin, min_ratio=0, min_pages=3)
    pages = strip_boilerplate(pages, footers, margin=layout.footer_margin)
    pages = [top_to_bottom(p) if p.number > layout.narrative_last_page else p for p in pages]
    repeated = repeated_titles(pages, layout)

    def heading_of(line: Line, page: Page) -> Heading | None:
        if page.number <= layout.narrative_last_page:
            return narrative_heading(line, layout)
        return financial_heading(line, page, repeated)

    pages = [hoist_page_title(p, lambda l, p=p: heading_of(l, p) is not None) for p in pages]
    root = SectionDraft("root", spec.title, 0, "", pages[0].number, pages[-1].number)
    sections: list[SectionDraft] = [root]
    body: dict[str, list[tuple[int, Line]]] = defaultdict(list)
    captions: dict[str, list[tuple[int, Line]]] = defaultdict(list)
    tables: list[tuple[str, int, list[Line], Page]] = []
    state = {"l1": None, "current": root, "last": None}  # last = (section, heading line) until body follows

    def open_section(level: int, title: str, page_no: int, line: Line | None) -> None:
        parent = root if level == 1 or state["l1"] is None else state["l1"]
        path = title if parent is root else f"{parent.title} › {title}"
        section = SectionDraft(f"s{len(sections)}", title, level, path, page_no, page_no, parent_key=parent.key)
        sections.append(section)
        if level == 1:
            state["l1"] = section
        state["current"] = section
        state["last"] = (section, line) if line is not None else None

    def add_heading(heading: Heading, line: Line, page: Page) -> None:
        last = state["last"]
        adjacent = last is not None and line.bbox[1] - last[1].bbox[3] < 0.6 * line.size
        if last and last[0].level == heading.level and last[0].page_start == page.number and (heading.level == 1 or adjacent):
            # one title split over lines: "SECTION I" + "MANAGEMENT" + …, or a wrapped bold heading
            section = last[0]
            section.title = f"{section.title} {heading.title}"
            section.path = section.title if section.parent_key == "root" else f"{state['l1'].title} › {section.title}"
            state["last"] = (section, line)
        else:
            open_section(heading.level, heading.title, page.number, line)
        if heading.rest is not None:
            body[state["current"].key].append((page.number, heading.rest))
            state["last"] = None

    standalone: tuple[SectionDraft, tuple] | None = None  # (section, state to resume after it)
    for page in pages:
        resume = None
        title = layout.standalone_pages.get(page.number)
        if title and standalone and standalone[0].title == title:
            resume = standalone[1]
            state["l1"] = state["current"] = standalone[0]
        elif title:
            resume = (state["l1"], state["current"])
            open_section(1, title, page.number, None)
            standalone = (state["current"], resume)
        elif page.number in layout.injected:
            open_section(1, layout.injected[page.number], page.number, None)
        table_lines: list[Line] = []
        for line in page.lines:
            heading = heading_of(line, page)
            if heading:
                add_heading(heading, line, page)
                continue
            state["last"] = None
            key = state["current"].key
            if page.number in layout.table_pages:
                if not in_margin(line, page) and line.text not in repeated:
                    table_lines.append(line)
            elif page.number <= layout.narrative_last_page and is_caption(line, layout):
                captions[key].append((page.number, line))
            else:
                body[key].append((page.number, line))
        if table_lines:
            tables.append((state["current"].key, page.number, table_lines, page))
        if resume is not None:
            state["l1"], state["current"] = resume
            state["last"] = None

    vocab = prepared.vocab or None
    chunks: list[ChunkDraft] = []
    by_key = {s.key: s for s in sections}

    def pseudo_pages(items: list[tuple[int, Line]]) -> list[Page]:
        grouped: dict[int, list[Line]] = defaultdict(list)
        for page_no, line in items:
            grouped[page_no].append(line)
        return [Page(n, pages[0].width, pages[0].height, tuple(ls)) for n, ls in sorted(grouped.items())]

    for section in sections:
        paragraphs = paragraphs_from_pages(pseudo_pages(body[section.key]), vocab)
        section.content = "\n\n".join(p.text for p in paragraphs) or None
        for packed in pack(paragraphs, TARGET_TOKENS, OVERLAP_TOKENS):
            chunks.append(ChunkDraft(section.key, "text", packed.text, packed.page_start, packed.page_end, refs=packed.refs))
        for caption in paragraphs_from_pages(pseudo_pages(captions[section.key]), vocab):
            chunks.append(ChunkDraft(section.key, "caption", caption.text, caption.page, caption.page))
    for key, page_no, lines, page in tables:
        rows = pad_numeric_rows(merge_symbol_cells(region_rows(replace(page, lines=tuple(lines)), (0, 0, page.width, page.height))))
        header_rows = next((i + 1 for i, row in enumerate(rows) if "2018" in row), 1)
        for piece in split_table(rows, header_rows, TABLE_TOKENS, count_tokens):
            chunks.append(ChunkDraft(key, "table", piece, page_no, page_no))

    for chunk in chunks:  # a section spans every page it holds content on
        section = by_key[chunk.section_key]
        section.page_end = max(section.page_end, chunk.page_end)
    for section in sections:
        if section.parent_key and section.parent_key != "root":
            parent = by_key[section.parent_key]
            parent.page_end = max(parent.page_end, section.page_end)
    return BuildResult(sections, chunks)

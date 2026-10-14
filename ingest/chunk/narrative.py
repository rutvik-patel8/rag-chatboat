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

from model import Line, Page, Span

_BOLD = re.compile(r"Bold", re.I)
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
    injected: dict[int, str] = field(default_factory=lambda: {2: "Letter of Transmittal", 7: "About GPO"})
    single_page_sections: dict[int, str] = field(default_factory=lambda: {14: "GPO By the Numbers FY 2018"})
    table_pages: frozenset[int] = frozenset({14, 58, 59, 60})


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
        is_bold(line)
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

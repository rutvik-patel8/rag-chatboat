"""Plain data types passed between the parsing, chunking and storage steps."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Span:
    text: str
    size: float
    flags: int
    bbox: BBox
    font: str = ""
    color: int = 0  # sRGB as 0xRRGGBB

    @property
    def is_superscript(self) -> bool:
        return bool(self.flags & 1)  # PyMuPDF TEXT_FONT_SUPERSCRIPT


@dataclass(frozen=True)
class Ref:
    """A footnote marker: note number `n` cited on `page`, at character `offset` of its line/paragraph text."""

    page: int
    n: int
    offset: int = 0


@dataclass(frozen=True)
class Line:
    spans: tuple[Span, ...]
    bbox: BBox
    block: int
    refs: tuple[Ref, ...] = ()  # markers removed from this line, offsets into `text`

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(span.text for span in self.spans)).strip()

    @property
    def size(self) -> float:
        """Font size of the span carrying most of the text (bullets and markers don't count)."""
        sized = [span for span in self.spans if span.text.strip()]
        if not sized:
            return 0.0
        return max(sized, key=lambda span: len(span.text.strip())).size


@dataclass(frozen=True)
class Page:
    number: int  # 1-based PDF page index
    width: float
    height: float
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Note:
    n: int
    page: int
    text: str


@dataclass
class SectionDraft:
    key: str
    title: str
    level: int
    path: str
    page_start: int
    page_end: int
    parent_key: str | None = None
    kind: str = "section"
    content: str | None = None


@dataclass
class ChunkDraft:
    section_key: str
    kind: str  # text | table | chart | callout | caption | footnote | section_summary
    content: str
    page_start: int
    page_end: int
    refs: tuple[tuple[int, int], ...] = ()  # (page, n) of each footnote marker in this chunk
    metadata: dict = field(default_factory=dict)
    footnotes: list[dict] = field(default_factory=list)  # [{n, text}], filled by the pipeline

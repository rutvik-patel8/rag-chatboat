"""Load a PDF into Page/Line/Span records with normalized text."""
from __future__ import annotations

import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

from model import Line, Page, Span


def normalize(text: str) -> str:
    """NFKC folds ligatures (ﬀ → ff) and non-breaking spaces so keyword search matches."""
    return unicodedata.normalize("NFKC", text)


def load_pages(pdf_path: Path | str) -> list[Page]:
    pages: list[Page] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            lines: list[Line] = []
            for block_no, block in enumerate(page.get_text("dict")["blocks"]):
                if block["type"] != 0:  # 1 = image block
                    continue
                for raw in block["lines"]:
                    spans = tuple(
                        Span(normalize(s["text"]), round(s["size"], 2), s["flags"], tuple(s["bbox"]))
                        for s in raw["spans"]
                        if s["text"]
                    )
                    if spans and "".join(span.text for span in spans).strip():
                        lines.append(Line(spans, tuple(raw["bbox"]), block_no))
            pages.append(Page(page.number + 1, page.rect.width, page.rect.height, tuple(lines)))
    return pages


def body_font_size(pages: list[Page]) -> float:
    """Most common font size, weighted by characters."""
    sizes: Counter[float] = Counter()
    for page in pages:
        for line in page.lines:
            for span in line.spans:
                sizes[round(span.size, 1)] += len(span.text.strip())
    return sizes.most_common(1)[0][0]

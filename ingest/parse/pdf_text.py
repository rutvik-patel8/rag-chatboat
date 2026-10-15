"""Load a PDF into Page/Line/Span records with normalized text."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

from model import Line, Page, Span


def normalize(text: str) -> str:
    """NFKC folds ligatures (ﬀ → ff) and non-breaking spaces so keyword search matches."""
    return unicodedata.normalize("NFKC", text)


WIDE_T = 0.85  # a plain "T" is 0.56–0.66× the font size wide; GEP's "Th" ligature glyph is 1.0–1.1×


def _span_text(span: dict) -> str:
    """Rebuild span text from glyphs. GEP maps its "Th" ligature glyph to a bare "T" ("Te", "Tird")."""
    chars = span["chars"]
    out = []
    for i, char in enumerate(chars):
        text = char["c"]
        width = char["bbox"][2] - char["bbox"][0]
        next_char = chars[i + 1]["c"] if i + 1 < len(chars) else ""
        if text == "T" and width > WIDE_T * span["size"] and next_char != "h":
            text = "Th"
        out.append(text)
    return "".join(out)


def load_pages(pdf_path: Path | str) -> list[Page]:
    pages: list[Page] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            lines: list[Line] = []
            for block_no, block in enumerate(page.get_text("rawdict")["blocks"]):
                if block["type"] != 0:  # 1 = image block
                    continue
                for raw in block["lines"]:
                    texts = [(_span_text(s), s) for s in raw["spans"]]
                    spans = tuple(
                        Span(normalize(text), round(s["size"], 2), s["flags"], tuple(s["bbox"]), s["font"], s["color"])
                        for text, s in texts
                        if text
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


_WORD = re.compile(r"[^\W\d_]+")
_TRAILING_HYPHEN = re.compile(r"([^\W\d_]+)-$")
_LEADING_WORD = re.compile(r"[^\W\d_]+")


def word_vocabulary(pages: list[Page]) -> frozenset[str]:
    """Lower-case words seen anywhere in the document, used to decide line-end hyphens."""
    return frozenset(w.lower() for page in pages for line in page.lines for w in _WORD.findall(line.text))


def join_lines(left: str, right: str, vocab: frozenset[str]) -> tuple[str, int]:
    """Join two wrapped lines; returns the text and the offset where `right` starts.

    A line-end hyphen is dropped only when the joined word appears elsewhere in the document
    ("house-" + "holds" → "households"); otherwise it is a real compound ("middle-income").
    """
    if left.endswith("-") and left[-2:-1].isdigit() and right[:1].isdigit():
        return left + right, len(left)  # "Public Law 103-" + "40" → "103-40"
    hyphen = _TRAILING_HYPHEN.search(left)
    word = _LEADING_WORD.match(right)
    if hyphen and right[:1].islower():
        if word and (hyphen.group(1) + word.group(0)).lower() in vocab:
            return left[:-1] + right, len(left) - 1
        return left + right, len(left)
    return f"{left} {right}", len(left) + 1

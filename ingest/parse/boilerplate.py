"""Remove running headers, footers and page numbers: margin lines that repeat across pages."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from model import Line, Page

MARGIN = 0.08


def margin_key(text: str) -> str:
    """Letter-spaced or numbered variants of one header map to the same key."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text.lower()))


def in_margin(line: Line, page: Page, margin: float = MARGIN) -> bool:
    return line.bbox[3] < margin * page.height or line.bbox[1] > (1 - margin) * page.height


def find_boilerplate(pages: list[Page], margin: float = MARGIN, min_ratio: float = 0.15, min_pages: int = 3) -> set[str]:
    seen: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        for line in page.lines:
            if in_margin(line, page, margin):
                seen[margin_key(line.text)].add(page.number)
    needed = max(min_pages, min_ratio * len(pages))
    return {key for key, on_pages in seen.items() if key and len(on_pages) >= needed}


def strip_boilerplate(pages: list[Page], keys: set[str], margin: float = MARGIN) -> list[Page]:
    return [
        replace(
            page,
            lines=tuple(l for l in page.lines if not (in_margin(l, page, margin) and margin_key(l.text) in keys)),
        )
        for page in pages
    ]

"""Page transcription with Gemini vision (spec §5.4): each page image becomes typed blocks."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf

from cache import JsonCache
from parse.pdf_text import normalize

PROMPT_VERSION = "v1"
DPI = 150
PROMPT = """You transcribe one page of a product environmental report into blocks, in reading order.
- kind: "text" for paragraphs and headings, "table" for tables (as markdown), "chart" for charts and graphs,
  "callout" for short highlighted facts (an icon or big number with a caption).
- Copy wording and numbers exactly. Do not summarize, explain or add anything.
- Charts: put the printed labels and totals in markdown, and list every segment in values. When a segment has no
  printed value, estimate it from the bar proportions anchored on the printed totals and set estimated=true;
  printed values have estimated=false.
- Superscript digits after words are footnote references: list them in footnote_refs and leave them out of the text."""
SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["text", "table", "chart", "callout"]},
                    "title": {"type": "string"},
                    "markdown": {"type": "string"},
                    "values": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "unit": {"type": "string"},
                                "estimated": {"type": "boolean"},
                            },
                            "required": ["label", "value", "estimated"],
                        },
                    },
                    "footnote_refs": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["kind", "markdown"],
            },
        }
    },
    "required": ["blocks"],
}

_SUPERSCRIPT_RUN = re.compile("[⁰¹²³⁴⁵⁶⁷⁸⁹]+")
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def render_page(pdf_path: Path | str, page_number: int, dpi: int = DPI) -> bytes:
    with pymupdf.open(pdf_path) as doc:
        return doc[page_number - 1].get_pixmap(dpi=dpi).tobytes("png")


def _join_wrapped_rows(markdown: str) -> str:
    """Models sometimes break a table cell across lines; a line not starting with "|" continues the row above."""
    rows: list[str] = []
    for line in markdown.splitlines():
        if rows and line.strip() and not line.lstrip().startswith("|"):
            rows[-1] = f"{rows[-1].rstrip()} {line.strip()}"
        else:
            rows.append(line)
    return "\n".join(rows)


def strip_markers(text: str, refs: set[int]) -> str:
    """Models often write a listed footnote marker as plain digits glued to a word: "emissions7", "STAR.^18"."""
    for n in sorted(refs, reverse=True):
        # after a letter or bracket, optionally behind a full stop ("STAR.^18") — never after a digit ("91.1%")
        text = re.sub(rf"(?<=[A-Za-z)\]])(\.?)\^?{n}\b", r"\1", text)
    return text


def clean_block(block: dict) -> dict:
    """Superscript footnote markers become refs (NFKC would turn "achieved⁷" into "achieved7"); text is normalized."""
    refs = set(block.get("footnote_refs") or [])

    def pull(text: str) -> str:
        refs.update(int(run.translate(_SUPERSCRIPT_DIGITS)) for run in _SUPERSCRIPT_RUN.findall(text))
        return normalize(_SUPERSCRIPT_RUN.sub("", text)).replace("**", "").strip()

    markdown = pull(block.get("markdown", ""))
    if block["kind"] == "table":
        markdown = _join_wrapped_rows(markdown)
    values = [
        {"label": pull(v["label"]), "value": pull(v["value"]), "unit": pull(v.get("unit", "")), "estimated": bool(v["estimated"])}
        for v in block.get("values") or []
    ]
    title = pull(block.get("title") or "")
    if markdown.lstrip("# ").startswith(title):
        title = ""  # the model repeated the heading inside the markdown
    clean = lambda text: strip_markers(text, refs)  # noqa: E731
    values = [{**v, "label": clean(v["label"])} for v in values]
    return {"kind": block["kind"], "title": clean(title), "markdown": clean(markdown), "values": values, "footnote_refs": sorted(refs)}


class PageDescriber:
    """Renders a page, asks the vision model for blocks, caches the raw answer by image hash."""

    def __init__(self, generator, cache: JsonCache, model: str):
        self.generator = generator
        self.cache = cache
        self.model = model

    def __call__(self, pdf_path: Path | str, page_number: int) -> list[dict]:
        png = render_page(pdf_path, page_number)
        key = JsonCache.key(self.model, PROMPT_VERSION, hashlib.sha256(png).hexdigest())
        blocks = self.cache.get(key)
        if blocks is None:
            blocks = self.generator.generate_json(PROMPT, SCHEMA, png)["blocks"]
            self.cache.put(key, blocks)
        return [clean_block(block) for block in blocks]

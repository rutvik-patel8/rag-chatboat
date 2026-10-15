"""Visual profile for the iMac Product Environmental Report (spec §5.4).

Most facts sit in charts, tables, icon call-outs and endnotes, so each page is transcribed by a vision model
into typed blocks. Numbers the model reads are checked against the PDF's own text layer.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from chunk.common import Paragraph, pack, paragraphs_from_pages
from model import ChunkDraft, Page, Ref, SectionDraft
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 400
OVERLAP_TOKENS = 50
TITLE_SIZE = 20.0  # page titles are 24pt
APPROXIMATE = "(approximate, read from chart)"
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def page_title(page: Page) -> str | None:
    lines = sorted((l for l in page.lines if l.size >= TITLE_SIZE), key=lambda l: l.bbox[1])
    return " ".join(l.text for l in lines) or None


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def verify(block: dict, page_numbers: set[str], page_no: int) -> tuple[dict, list[str]]:
    """A chart value the page doesn't print is marked approximate; other unseen numbers are reported."""
    warnings = []
    values = []
    for value in block["values"]:
        if not value["estimated"] and not numbers(value["value"]) <= page_numbers:
            value = {**value, "estimated": True}
            warnings.append(f"p{page_no}: chart value {value['label']}={value['value']} not printed; marked approximate")
        values.append(value)
    unseen = sorted(numbers(block["markdown"]) - page_numbers)
    if unseen and block["kind"] in ("table", "callout"):
        warnings.append(f"p{page_no}: {block['kind']} numbers not in the text layer: {', '.join(unseen)}")
    return {**block, "values": values}, warnings


def block_content(block: dict) -> str:
    parts = [block["title"], block["markdown"]]
    if block["values"] and block["kind"] == "chart":
        parts.append(
            "Values: "
            + "; ".join(
                f"{v['label']}: {v['value']}{(' ' + v['unit']) if v['unit'] else ''}{(' ' + APPROXIMATE) if v['estimated'] else ''}"
                for v in block["values"]
            )
        )
    return "\n\n".join(p for p in parts if p)


def table_notes(block: dict, notes: list) -> list[int]:
    """On an endnote page, a table belongs to the note whose text names at least two of its row labels."""
    labels = [row.split("|")[1].strip() for row in block["markdown"].splitlines() if row.count("|") >= 3 and "---" not in row]
    labels = [label for label in labels if len(label) > 2]
    return [note.n for note in notes if sum(label in note.text for label in labels) >= 2]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def attach_missing_refs(page: Page, paragraphs: list[Paragraph], indices: list[int], page_chunks: list[ChunkDraft]) -> None:
    """Vision sometimes drops a footnote marker entirely; the text layer still has it. Attach each missing ref to
    the block holding the words just before the marker, else to the page's first paragraph or chunk."""
    seen = {r.n for i in indices for r in paragraphs[i].refs} | {n for c in page_chunks for _, n in c.refs}
    for line in page.lines:
        for ref in line.refs:
            if ref.n in seen:
                continue
            before = _squash(line.text[: ref.offset])[-25:].strip()
            index = next((i for i in indices if before and before in _squash(paragraphs[i].text)), None)
            chunk = next((c for c in page_chunks if before and before in _squash(c.content)), None) if index is None else None
            if index is None and chunk is None:
                index = indices[0] if indices else None
                chunk = page_chunks[0] if index is None and page_chunks else None
            if index is not None:
                p = paragraphs[index]
                paragraphs[index] = replace(p, refs=p.refs + (Ref(page.number, ref.n, len(p.text)),))
            elif chunk is not None:
                chunk.refs = chunk.refs + ((page.number, ref.n),)
            seen.add(ref.n)


def build(spec: DocSpec, prepared: Prepared) -> BuildResult:
    pages = prepared.pages
    raw = {p.number: p for p in prepared.raw_pages}
    root = SectionDraft("root", spec.title, 0, "", pages[0].number, pages[-1].number)
    sections: list[SectionDraft] = [root]
    paragraphs: dict[str, list[Paragraph]] = defaultdict(list)
    chunks: list[ChunkDraft] = []
    warnings: list[str] = []
    current = root
    for page in pages:
        title = page_title(page)
        if title and title != current.title:
            current = SectionDraft(f"s{len(sections)}", title, 1, title, page.number, page.number, "root")
            sections.append(current)
        current.page_end = page.number
        blocks = None
        if prepared.describe is not None:
            try:
                blocks = prepared.describe(page.number)
            except Exception as exc:
                reason = (str(exc).splitlines() or [type(exc).__name__])[0][:100]
                warnings.append(f"p{page.number}: vision failed ({reason}); used the text layer")
        if blocks is None:
            paragraphs[current.key] += paragraphs_from_pages([page], prepared.vocab or None)
            continue
        page_numbers = numbers(" ".join(l.text for l in raw.get(page.number, page).lines))
        endnotes = page.number in spec.endnote_pages
        first_paragraph, first_chunk = len(paragraphs[current.key]), len(chunks)
        for block in blocks:
            if endnotes and block["kind"] != "table":
                continue  # endnote text is already indexed as footnote chunks by the pipeline
            block, problems = verify(block, page_numbers, page.number)
            warnings += problems
            refs = tuple((page.number, n) for n in block["footnote_refs"])
            if endnotes:  # table inside an endnote (iMac note 18's power table)
                on_page = [note for note in prepared.notes.notes if note.page == page.number]
                refs = tuple((page.number, n) for n in table_notes(block, on_page))
            if block["kind"] == "text":
                text = block_content(block).lstrip("# ").strip()
                if text:
                    paragraphs[current.key].append(Paragraph(text, page.number, tuple(Ref(page.number, n, len(text)) for n in block["footnote_refs"])))
            else:
                metadata = {"estimated": True} if any(v["estimated"] for v in block["values"]) else {}
                chunks.append(ChunkDraft(current.key, block["kind"], block_content(block), page.number, page.number, refs=refs, metadata=metadata))
        if not endnotes:
            indices = list(range(first_paragraph, len(paragraphs[current.key])))
            attach_missing_refs(page, paragraphs[current.key], indices, chunks[first_chunk:])
    for section in sections:
        section.content = "\n\n".join(p.text for p in paragraphs[section.key]) or None
        for packed in pack(paragraphs[section.key], TARGET_TOKENS, OVERLAP_TOKENS):
            chunks.append(ChunkDraft(section.key, "text", packed.text, packed.page_start, packed.page_end, refs=packed.refs))
    return BuildResult(sections, chunks, warnings)

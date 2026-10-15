"""Visual profile for the iMac Product Environmental Report (spec §5.4).

Most facts sit in charts, tables, icon call-outs and endnotes, so each page is transcribed by a vision model
into typed blocks. Numbers the model reads are checked against the PDF's own text layer.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pymupdf

from chunk.common import Paragraph, pack, paragraphs_from_pages
from model import ChunkDraft, Page, Ref, SectionDraft
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 400
OVERLAP_TOKENS = 50
TITLE_SIZE = 20.0  # page titles are 24pt
APPROXIMATE = "(approximate, read from chart)"
MEASURED = "(measured from the chart's shapes)"
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_TOTAL = re.compile(r"([-–−]?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%]+)")


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
        if not value["estimated"] and not block.get("measured") and not numbers(value["value"]) <= page_numbers:
            value = {**value, "estimated": True}
            warnings.append(f"p{page_no}: chart value {value['label']}={value['value']} not printed; marked approximate")
        values.append(value)
    estimates = [v for v in block["values"] if v["estimated"]]  # the model's own estimates, "anchored on printed totals"
    total = _TOTAL.search(block["markdown"]) if block["kind"] == "chart" else None
    if estimates and total and all(_NUMBER.fullmatch(v["value"]) for v in estimates):
        printed = _to_number(total.group(1))
        summed = sum(_to_number(v["value"]) for v in estimates)
        if printed > 0 and abs(summed - printed) > 0.03 * printed:  # the prompt says "anchored on printed totals"
            warnings.append(f"p{page_no}: chart estimates sum to {summed:g} vs printed {printed:g}; values dropped")
            values = [v for v, original in zip(values, block["values"]) if not original["estimated"]]
    unseen = sorted(numbers(block["markdown"]) - page_numbers)
    if unseen and block["kind"] in ("table", "callout"):
        warnings.append(f"p{page_no}: {block['kind']} numbers not in the text layer: {', '.join(unseen)}")
    return {**block, "values": values}, warnings


def _to_number(text: str) -> float:
    return float(text.replace("–", "-").replace("−", "-").replace(",", ""))


def _bar_columns(pdf_path: Path | str, page_number: int) -> list[tuple[int, int, list[tuple[float, float]]]]:
    """Filled rectangles stacked in one x-range: (x0, x1, [(y0, y1), …]) per bar, top to bottom."""
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_number - 1]
        width, drawings = page.rect.width, page.get_drawings()
    columns: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for drawing in drawings:
        if drawing.get("fill") is None:
            continue
        for item in drawing["items"]:
            if item[0] == "re" and 20 < item[1].width < 0.5 * width and item[1].height > 3:
                columns[(round(item[1].x0), round(item[1].x1))].append((item[1].y0, item[1].y1))
    return [(x0, x1, sorted(segments)) for (x0, x1), segments in sorted(columns.items()) if len(segments) >= 2]


def measure_bars(pdf_path: Path | str, page: Page) -> list[dict]:
    """Exact segment values for vector stacked-bar charts: each segment's height scaled to its bar's printed total.

    A bar is a column of filled rectangles with a printed total ("346 kg") just above it; segment labels sit inside
    the rectangles. Bars with a negative total (a waterfall's reductions) use the median scale of the others."""

    def center(line):
        return (line.bbox[0] + line.bbox[2]) / 2, (line.bbox[1] + line.bbox[3]) / 2

    bars = []
    for x0, x1, segments in _bar_columns(pdf_path, page.number):
        top = segments[0][0]
        above = sorted(
            (l for l in page.lines if x0 - 5 <= center(l)[0] <= x1 + 5 and 0 <= top - l.bbox[3] < 45),
            key=lambda l: l.bbox[1],
        )
        total_line = next((l for l in above if _TOTAL.fullmatch(l.text)), None)
        if total_line is None:
            continue
        amount, unit = _TOTAL.fullmatch(total_line.text).groups()
        labelled = []
        refs = {r.n for l in above for r in l.refs}
        for y0, y1 in segments:
            inside = [l for l in page.lines if x0 <= center(l)[0] <= x1 and y0 <= center(l)[1] <= y1]
            refs |= {r.n for l in inside for r in l.refs}
            labelled.append((" ".join(l.text for l in inside) or "unlabelled segment", y1 - y0))
        title = " ".join(l.text for l in above if l is not total_line)
        bars.append((title, total_line.text, _to_number(amount), unit, labelled, sorted(refs)))
    scales = [total / sum(h for _, h in segs) for _, _, total, _, segs, _ in bars if total > 0]
    if not scales:
        return []
    blocks = []
    for title, total_text, total, unit, segs, refs in bars:
        height = sum(h for _, h in segs)
        scale = total / height if total > 0 else statistics.median(scales)
        values = []
        for label, h in segs:
            value = {"label": label, "value": f"{h * scale:.0f}", "unit": unit, "estimated": False}
            if total > 0:
                value["share"] = round(100 * h / height)
            values.append(value)
        blocks.append({"kind": "chart", "title": "", "markdown": f"{title}: {total_text}", "values": values,
                       "footnote_refs": refs, "measured": True})
    return blocks


def block_content(block: dict) -> str:
    parts = [block["title"], block["markdown"]]
    if block["values"] and block.get("measured"):
        items = [f"{v['label']}: {v['value']} {v['unit']}" + (f" ({v['share']}%)" if "share" in v else "") for v in block["values"]]
        parts.append(f"Values {MEASURED}: " + "; ".join(items))
    elif block["values"] and block["kind"] == "chart":
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
        measured = measure_bars(spec.path, page)
        if measured:  # exact values from the vector shapes beat the model's estimates
            blocks = [b for b in blocks if b["kind"] != "chart"] + measured
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
                metadata = {"measured": True} if block.get("measured") else {"estimated": True} if any(v["estimated"] for v in block["values"]) else {}
                chunks.append(ChunkDraft(current.key, block["kind"], block_content(block), page.number, page.number, refs=refs, metadata=metadata))
        if not endnotes:
            indices = list(range(first_paragraph, len(paragraphs[current.key])))
            attach_missing_refs(page, paragraphs[current.key], indices, chunks[first_chunk:])
    for section in sections:
        section.content = "\n\n".join(p.text for p in paragraphs[section.key]) or None
        for packed in pack(paragraphs[section.key], TARGET_TOKENS, OVERLAP_TOKENS):
            chunks.append(ChunkDraft(section.key, "text", packed.text, packed.page_start, packed.page_end, refs=packed.refs))
    return BuildResult(sections, chunks, warnings)

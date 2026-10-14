# Ingestion Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared ingestion machinery every document profile uses: PDF text loading, boilerplate stripping, footnote/marker handling, table row reconstruction, paragraph packing, an embedding cache, a transactional store, and an `ingest.py` CLI. Prove it end-to-end with a simple `plain` profile on the iMac PDF.

**Architecture:** `parse/` turns a PDF into immutable `Page → Line → Span` records (NFKC-normalized) and offers page-level transforms: strip repeated margin lines, split footnote text out, record superscript markers as `Line.refs`, rebuild table rows from a region. `chunk/common.py` turns lines into paragraphs and packs them into token-bounded chunks. `pipeline.py` runs the shared steps, calls a profile (`(DocSpec, Prepared) -> BuildResult`), attaches footnote text, embeds through a disk cache, and stores everything in one transaction. Profiles for GPO/GEP/iMac arrive in sub-projects 3–5; until then all three documents use `plain`.

**Tech Stack:** Python 3 + uv, PyMuPDF (`import pymupdf`), tiktoken (`cl100k_base`), psycopg 3 + pgvector, pytest. Builds on sub-project 1 (`config`, `db`, `gemini`).

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md` — this plan implements §10 row 2 using §4, §5.1, §5.5, §7, §9. §5.1 was updated from real-PDF probes before this plan (row reconstruction instead of `find_tables()`, 15% boilerplate threshold, footnote rules).

## Global Constraints

- Run every command from `ingest/` with `uv run`. Docker DB must be up (`docker compose up -d --wait db` from the repo root).
- Page numbers are **1-based PDF page indexes** everywhere (GEP "p95" = PDF page 95).
- All extracted text is NFKC-normalized at load time.
- Boilerplate: lines in the top/bottom **8%** of a page, key = lower-case, whitespace removed, digit runs → `#`; stripped when repeated on **≥ 15% of pages (min 3)**.
- Footnote start: first non-blank span is 1–3 digits with size **< 0.8×** the next span; line size **< body size**. Marker: superscript-flag span (`flags & 1`) matching digits or digit lists, size **< 0.85×** the line's main size.
- Marker resolution: same page, else the document's `endnote_pages`, else unresolved (reported). No nearest-page fallback.
- Table cells split at horizontal gaps **> 6pt**; rows group spans whose vertical centres differ by **≤ 2pt**.
- `plain` profile chunks: target **500** tokens, overlap **75** (spec §5.2 narrative sizes). Any embed text over **2000** cl100k tokens is an error (Gemini embedding input limit is 2048).
- `embed_text` format (spec §4): `"<doc title> › <section path> | p.<start>[-<end>] | <kind>\n\n<content>"`; when the section path is empty: `"<doc title> | p.<start>[-<end>] | <kind>\n\n<content>"`.
- Embedding cache lives in `ingest/.cache/<slug>/embeddings/` (git-ignored); `--reset` deletes `ingest/.cache/<slug>/`.
- Store refuses to write a document whose embedding model/dim differs from documents already stored (spec §3 contract).
- Commits: one per task, short imperative subject only (≤ 50 chars). **No `Co-Authored-By` trailer, no body.**

## Review Focus

1. **Ligatures and odd Unicode in source text** (GEP prints `aﬀected`, `conﬂict`; iMac uses non-breaking spaces) → text is normalized so keyword search finds `affected`, `58 percent`. Pinned in Task 1.
2. **GEP table/figure superscripts with no footnote on that page** (148 markers vs 32 page footnotes) → marker stays unresolved and is reported, never attached to an unrelated note elsewhere. Pinned in Task 3.
3. **Re-running ingestion, or a crash mid-store** → no duplicate rows; the previously stored version survives a failed run. Pinned in Task 7.
4. **Oversized units** (a 1,300-token annex paragraph with no sentence breaks, a huge table) → split to the target, and anything still over the embedding limit fails loudly before any API call. Pinned in Tasks 4, 5 and 8.
5. **Documents with empty pages or no footnotes** (photo-only pages, GPO has zero notes) → pipeline still produces a root section spanning the remaining pages and zero footnote chunks without error. Pinned in Task 8.

---

### Task 1: Page model and PDF loader

**Files:**
- Modify: `ingest/pyproject.toml` (via `uv add`)
- Create: `ingest/model.py`
- Create: `ingest/parse/__init__.py` (empty)
- Create: `ingest/parse/pdf_text.py`
- Create: `ingest/tests/helpers.py`
- Create: `ingest/tests/conftest.py`
- Test: `ingest/tests/test_pdf_text.py`

**Interfaces:**
- Consumes: `config.REPO_ROOT` (sub-project 1).
- Produces:
  - `model.BBox = tuple[float, float, float, float]`
  - `model.Span(text: str, size: float, flags: int, bbox: BBox)` frozen; property `is_superscript: bool`
  - `model.Line(spans: tuple[Span, ...], bbox: BBox, block: int, refs: tuple[int, ...] = ())` frozen; properties `text: str` (whitespace-collapsed, stripped), `size: float` (size of the span with the most non-blank characters; `0.0` if none)
  - `model.Page(number: int, width: float, height: float, lines: tuple[Line, ...])` frozen
  - `model.Note(n: int, page: int, text: str)` frozen
  - `model.SectionDraft(key, title, level, path, page_start, page_end, parent_key=None, kind="section", content=None)` mutable dataclass
  - `model.ChunkDraft(section_key, kind, content, page_start, page_end, refs=(), metadata={}, footnotes=[])` mutable dataclass
  - `parse.pdf_text.normalize(text: str) -> str`, `load_pages(pdf_path) -> list[Page]`, `body_font_size(pages) -> float`
  - test helpers `tests/helpers.py`: `make_line(text, y0=100.0, size=10.0, block=0, x0=50.0, flags=0, refs=())`, `make_page(number, lines, width=600.0, height=800.0)`, `line_with(page, text) -> Line`
  - session fixtures in `tests/conftest.py`: `imac_pages`, `gep_pages`, `gpo_pages`

- [ ] **Step 1: Add dependencies**

Run: `cd ingest && uv add "pymupdf>=1.28" "tiktoken>=0.9"`
Expected: `pyproject.toml` dependencies gain both packages, `uv.lock` updates, exit 0. (The PyMuPDF wheel is ~25 MB; allow several minutes on a slow link.)

- [ ] **Step 2: Write test helpers and fixtures**

`ingest/tests/helpers.py`:
```python
from model import Line, Page, Span


def make_line(text, y0=100.0, size=10.0, block=0, x0=50.0, flags=0, refs=()):
    bbox = (x0, y0, x0 + 6 * len(text), y0 + size * 1.2)
    return Line((Span(text, size, flags, bbox),), bbox, block, tuple(refs))


def make_page(number, lines, width=600.0, height=800.0):
    return Page(number, width, height, tuple(lines))


def line_with(page, text):
    return next(line for line in page.lines if text in line.text)
```

`ingest/tests/conftest.py`:
```python
import pytest

from config import REPO_ROOT
from parse.pdf_text import load_pages

DOCS = REPO_ROOT / "docs"


@pytest.fixture(scope="session")
def imac_pages():
    return load_pages(DOCS / "iMac_PER_Oct2024.pdf")


@pytest.fixture(scope="session")
def gep_pages():
    return load_pages(DOCS / "GEP-Jun-2026.pdf")


@pytest.fixture(scope="session")
def gpo_pages():
    return load_pages(DOCS / "GPO-Anual-report.pdf")
```

- [ ] **Step 3: Write the failing tests**

`ingest/tests/test_pdf_text.py`:
```python
import pytest

from helpers import line_with
from model import Line, Span
from parse.pdf_text import body_font_size


def test_line_text_collapses_whitespace():
    line = Line((Span("  iMac\tuses ", 8.5, 4, (0, 0, 1, 1)), Span("58  percent ", 8.5, 4, (1, 0, 2, 1))), (0, 0, 2, 1), 0)
    assert line.text == "iMac uses 58 percent"


def test_line_size_is_the_longest_span_not_the_largest():
    bullet = Span("•", 8.4, 4, (0, 0, 1, 1))
    words = Span(" Sleep: Low power state", 7.0, 4, (1, 0, 9, 1))
    assert Line((bullet, words), (0, 0, 9, 1), 0).size == 7.0


def test_superscript_flag_is_bit_zero():
    assert Span("18", 5.7, 5, (0, 0, 1, 1)).is_superscript
    assert not Span("18", 5.7, 4, (0, 0, 1, 1)).is_superscript


def test_pages_are_numbered_from_one(imac_pages):
    assert [p.number for p in imac_pages] == list(range(1, 13))


def test_ligatures_and_nbsp_are_normalised(gep_pages, imac_pages):
    assert "affected by the conflict" in line_with(gep_pages[42], "by the conflict").text
    assert not any("ﬀ" in line.text or "ﬂ" in line.text for page in gep_pages for line in page.lines)
    assert "58 percent less energy" in line_with(imac_pages[6], "iMac consumes").text


@pytest.mark.parametrize("fixture, expected", [("gpo_pages", 12.0), ("gep_pages", 11.0), ("imac_pages", 8.5)])
def test_body_font_size(request, fixture, expected):
    assert body_font_size(request.getfixturevalue(fixture)) == expected


def test_superscript_marker_survives_loading(imac_pages):
    line = line_with(imac_pages[6], "iMac consumes")
    assert [s.text for s in line.spans if s.is_superscript] == ["18"]
```

- [ ] **Step 4: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_pdf_text.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model'`

- [ ] **Step 5: Write `ingest/model.py`**

```python
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

    @property
    def is_superscript(self) -> bool:
        return bool(self.flags & 1)  # PyMuPDF TEXT_FONT_SUPERSCRIPT


@dataclass(frozen=True)
class Line:
    spans: tuple[Span, ...]
    bbox: BBox
    block: int
    refs: tuple[int, ...] = ()  # footnote numbers whose markers were removed from this line

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
    refs: tuple[int, ...] = ()
    metadata: dict = field(default_factory=dict)
    footnotes: list[dict] = field(default_factory=list)  # [{n, text}], filled by the pipeline
```

- [ ] **Step 6: Write `ingest/parse/__init__.py` (empty) and `ingest/parse/pdf_text.py`**

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_pdf_text.py -q -p no:warnings`
Expected: 9 passed

- [ ] **Step 8: Commit**

```bash
git add ingest/pyproject.toml ingest/uv.lock ingest/model.py ingest/parse/__init__.py ingest/parse/pdf_text.py ingest/tests/helpers.py ingest/tests/conftest.py ingest/tests/test_pdf_text.py
git commit -m "Add page model and PDF loader"
```

---

### Task 2: Boilerplate stripping

**Files:**
- Create: `ingest/parse/boilerplate.py`
- Test: `ingest/tests/test_boilerplate.py`

**Interfaces:**
- Consumes: `model.Page`, `model.Line`; fixtures and helpers from Task 1.
- Produces: `parse.boilerplate.MARGIN = 0.08`, `margin_key(text: str) -> str`, `in_margin(line, page, margin=MARGIN) -> bool`, `find_boilerplate(pages, margin=MARGIN, min_ratio=0.15, min_pages=3) -> set[str]`, `strip_boilerplate(pages, keys, margin=MARGIN) -> list[Page]`

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_boilerplate.py`:
```python
from helpers import make_line, make_page
from parse.boilerplate import find_boilerplate, margin_key, strip_boilerplate


def test_margin_key_ignores_letter_spacing_case_and_numbers():
    spaced = "U . S . G O V E R N M E N T  P U B L I S H I N G  O F F I C E | 2 0 1 8  A N N U A L  R E P O R T"
    assert margin_key(spaced) == "u.s.governmentpublishingoffice|#annualreport"
    assert margin_key("CHAPTER 3") == margin_key("Chapter 1") == "chapter#"


def header_pages(total, with_header):
    pages = []
    for n in range(1, total + 1):
        lines = [make_line("Body text on this page", y0=400)]
        if n <= with_header:
            lines.append(make_line("Running Header", y0=20))
        pages.append(make_page(n, lines))
    return pages


def test_header_on_too_few_pages_is_kept():
    assert find_boilerplate(header_pages(10, 2)) == set()


def test_header_on_min_pages_is_found_and_only_margin_lines_are_removed():
    pages = header_pages(10, 3)
    pages.append(make_page(11, [make_line("Running Header", y0=400)]))  # same words, mid-page: real content
    keys = find_boilerplate(pages)
    assert keys == {"runningheader"}
    stripped = strip_boilerplate(pages, keys)
    assert [l.text for l in stripped[0].lines] == ["Body text on this page"]
    assert [l.text for l in stripped[10].lines] == ["Running Header"]


def test_gep_running_headers_are_removed(gep_pages):
    keys = find_boilerplate(gep_pages)
    assert {"globaleconomicprospects|june#", "chapter#", "#"} <= keys
    stripped = strip_boilerplate(gep_pages, keys)
    texts = [l.text for l in stripped[51].lines]
    assert "GLOBAL ECONOMIC PROSPECTS | JUNE 2026" not in texts
    assert any("natural gas production and exports led to a" in t for t in (l.text for l in stripped[40].lines))


def test_gpo_alternating_letter_spaced_headers_are_found(gpo_pages):
    keys = find_boilerplate(gpo_pages)
    assert "u.s.governmentpublishingoffice|#annualreport" in keys
    assert "#annualreport|u.s.governmentpublishingoffice" in keys


def test_imac_footer_is_found(imac_pages):
    assert "imac|productenvironmentalreport" in find_boilerplate(imac_pages)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_boilerplate.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'parse.boilerplate'`

- [ ] **Step 3: Write `ingest/parse/boilerplate.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_boilerplate.py -q -p no:warnings`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/parse/boilerplate.py ingest/tests/test_boilerplate.py
git commit -m "Strip repeated headers and footers"
```

---

### Task 3: Footnotes and markers

**Files:**
- Create: `ingest/parse/footnotes.py`
- Test: `ingest/tests/test_footnotes.py`

**Interfaces:**
- Consumes: `model.Line`, `model.Page`, `model.Note`, `model.Span`; `parse.pdf_text.body_font_size`; Task 1 fixtures/helpers.
- Produces:
  - `parse.footnotes.note_number(line: Line) -> int | None`
  - `parse.footnotes.split_notes(page: Page, body_size: float) -> tuple[list[Note], Page]` — returns the notes and the page without note lines
  - `parse.footnotes.strip_markers(page: Page) -> Page` — removes marker spans, sets `Line.refs`
  - `parse.footnotes.NoteIndex(notes: list[Note], endnote_pages: frozenset[int] = frozenset())` with `.notes: list[Note]` and `.resolve(n: int, page: int) -> Note | None`

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_footnotes.py`:
```python
from helpers import line_with, make_line, make_page
from model import Line, Note, Span
from parse.footnotes import NoteIndex, note_number, split_notes, strip_markers
from parse.pdf_text import body_font_size


def two_span_line(number, number_size, text, text_size):
    return Line(
        (Span(number, number_size, 4, (209, 98, 213, 105)), Span(text, text_size, 4, (217, 98, 400, 106))),
        (209, 98, 400, 106),
        0,
    )


def test_note_number_needs_a_small_number_before_larger_text():
    assert note_number(two_span_line("18", 4.7, "Energy consumption values", 7.0)) == 18
    assert note_number(two_span_line("2023", 4.7, "was a year", 7.0)) is None  # 4 digits
    assert note_number(two_span_line("18", 7.0, "Energy consumption", 7.0)) is None  # same size
    assert note_number(make_line("18 Energy consumption values")) is None  # single span


def notes_of(pages, numbers):
    body = body_font_size(pages)
    found = []
    for n in numbers:
        found += split_notes(pages[n - 1], body)[0]
    return found


def test_imac_endnotes_are_all_found(imac_pages):
    notes = notes_of(imac_pages, [11, 12])
    assert [n.n for n in notes] == list(range(1, 20))
    assert notes[0].text.startswith("Product recycled or renewable content is the mass")
    note18 = next(n for n in notes if n.n == 18)
    assert note18.page == 12
    assert "ENERGY STAR Program Requirements" in note18.text
    assert "Power adapter efficiency" in note18.text  # bullet lines continue the note


def test_gep_page_footnote_is_joined_across_lines(gep_pages):
    [note] = notes_of(gep_pages, [41])
    assert note == Note(1, 41, "Debt refers to aggregate non-financial sector debt as measured by total social financing (excluding equity).")


def test_gep_has_32_page_footnotes(gep_pages):
    assert len(notes_of(gep_pages, range(1, 201))) == 32


def test_split_notes_removes_note_lines_from_the_page(imac_pages):
    _, rest = split_notes(imac_pages[10], body_font_size(imac_pages))
    assert not any(l.text.startswith("1 Product recycled") for l in rest.lines)


def test_imac_marker_is_removed_and_recorded(imac_pages):
    page = strip_markers(split_notes(imac_pages[6], 8.5)[1])
    line = line_with(page, "iMac consumes")
    assert line.text == "on the market. iMac consumes 58 percent less energy than the requirement for ENERGY STAR."
    assert line.refs == (18,)


def test_gep_marker_is_removed_and_recorded(gep_pages):
    page = strip_markers(split_notes(gep_pages[40], 11.0)[1])
    line = line_with(page, "potential growth.")
    assert line.text == "potential growth. As exports ease, medium-term"
    assert line.refs == (1,)


def test_marker_lists_and_non_digit_superscripts():
    body = Span("Recycled cobalt", 8.5, 4, (0, 0, 60, 10))
    listed = Span("12,13", 5.0, 5, (60, 0, 70, 8))
    trademark = Span("TM", 5.0, 5, (70, 0, 80, 8))
    page = make_page(4, [Line((body, listed, trademark), (0, 0, 80, 10), 0)])
    [line] = strip_markers(page).lines
    assert line.refs == (12, 13)
    assert line.text == "Recycled cobaltTM"


def test_resolve_same_page_first_then_endnote_pages():
    notes = [Note(1, 41, "chapter 1 note"), Note(1, 110, "chapter 3 note"), Note(7, 11, "endnote 7")]
    index = NoteIndex(notes, endnote_pages=frozenset({11, 12}))
    assert index.resolve(1, 110).text == "chapter 3 note"
    assert index.resolve(7, 2).text == "endnote 7"


def test_unmatched_marker_is_unresolved_not_guessed():
    index = NoteIndex([Note(2, 43, "an unrelated page footnote")])
    assert index.resolve(2, 26) is None  # e.g. a GEP table-note marker on p26
    assert index.resolve(9, 43) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_footnotes.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'parse.footnotes'`

- [ ] **Step 3: Write `ingest/parse/footnotes.py`**

```python
"""Footnotes: note text (page bottom or endnote pages) and the superscript markers that cite them."""
from __future__ import annotations

import re
from dataclasses import replace

from model import Line, Note, Page

_NUMBER = re.compile(r"\d{1,3}")
_MARKER = re.compile(r"\s*\d{1,3}(?:\s*,\s*\d{1,3})*\s*")


def note_number(line: Line) -> int | None:
    """A note line starts with a small number followed by larger text, e.g. '18 Energy consumption…'."""
    spans = [span for span in line.spans if span.text.strip()]
    if len(spans) < 2:
        return None
    number, text = spans[0], spans[1]
    if _NUMBER.fullmatch(number.text.strip()) and number.size < 0.8 * text.size:
        return int(number.text.strip())
    return None


def split_notes(page: Page, body_size: float) -> tuple[list[Note], Page]:
    found: list[dict] = []
    keep: list[Line] = []
    current: dict | None = None
    for line in page.lines:
        n = note_number(line)
        if n is not None and line.size < body_size:
            spans = [span for span in line.spans if span.text.strip()][1:]
            current = {
                "n": n,
                "parts": [re.sub(r"\s+", " ", "".join(span.text for span in spans)).strip()],
                "size": line.size,
                "bottom": line.bbox[3],
            }
            found.append(current)
            continue
        gap = line.bbox[1] - current["bottom"] if current else None
        if current and abs(line.size - current["size"]) < 0.3 and -current["size"] <= gap < 1.5 * current["size"]:
            current["parts"].append(line.text)
            current["bottom"] = line.bbox[3]
            continue
        if line.size >= body_size:
            current = None
        keep.append(line)
    notes = [Note(item["n"], page.number, " ".join(item["parts"])) for item in found]
    return notes, replace(page, lines=tuple(keep))


def strip_markers(page: Page) -> Page:
    lines: list[Line] = []
    for line in page.lines:
        refs: list[int] = []
        spans = []
        for span in line.spans:
            if span.is_superscript and _MARKER.fullmatch(span.text) and span.size < 0.85 * line.size:
                refs.extend(int(n) for n in re.findall(r"\d+", span.text))
            else:
                spans.append(span)
        lines.append(replace(line, spans=tuple(spans), refs=tuple(refs)) if refs else line)
    return replace(page, lines=tuple(lines))


class NoteIndex:
    def __init__(self, notes: list[Note], endnote_pages: frozenset[int] = frozenset()):
        self.notes = list(notes)
        self.endnote_pages = endnote_pages

    def resolve(self, n: int, page: int) -> Note | None:
        """Same page first, then the endnote pages. Never guess from other pages: numbering restarts."""
        for note in self.notes:
            if note.n == n and note.page == page:
                return note
        for note in self.notes:
            if note.n == n and note.page in self.endnote_pages:
                return note
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_footnotes.py -q -p no:warnings`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/parse/footnotes.py ingest/tests/test_footnotes.py
git commit -m "Split footnotes and record markers"
```

---

### Task 4: Table row reconstruction

**Files:**
- Create: `ingest/parse/tables.py`
- Test: `ingest/tests/test_tables.py`

**Interfaces:**
- Consumes: `model.Page`, `model.Line`, `model.BBox`; Task 1 fixtures/helpers.
- Produces:
  - `parse.tables.region_rows(page: Page, bbox: BBox, row_tolerance: float = 2.0, cell_gap: float = 6.0) -> list[list[str]]`
  - `parse.tables.rows_to_markdown(rows: list[list[str]]) -> str` (first row is the header)
  - `parse.tables.split_table(rows, header_rows: int, max_tokens: int, count: Callable[[str], int]) -> list[str]` — markdown pieces, header repeated, never splits a row
  - `parse.tables.remove_region(page: Page, bbox: BBox) -> Page`

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_tables.py`:
```python
from helpers import make_line, make_page
from parse.tables import region_rows, remove_region, rows_to_markdown, split_table


def first_line(page, exact):
    return next(l for l in page.lines if l.text == exact)


def test_imac_power_table_rows(imac_pages):
    page = imac_pages[11]
    top = first_line(page, "Power consumption for iMac")
    bottom = first_line(page, "Power adapter efficiency")
    rows = region_rows(page, (0, top.bbox[1] - 1, page.width, bottom.bbox[3] + 1))
    assert rows == [
        ["Power consumption for iMac"],
        ["Mode", "100V", "115V", "230V"],
        ["Off", "0.27W", "0.27W", "0.29W"],
        ["Sleep", "0.56W", "0.57W", "0.59W"],
        ["Idle—Display On", "20.5W", "20.6W", "20.9W"],
        ["Power adapter, no load", "0.10W", "0.10W", "0.12W"],
        ["Power adapter efficiency", "91.1%", "91.5%", "91.9%"],
    ]


def test_gep_table_1_1_rows(gep_pages):
    page = gep_pages[25]
    top = next(l for l in page.lines if l.text.startswith("TABLE 1.1"))
    rows = region_rows(page, (0, top.bbox[1] - 1, page.width, page.height * 0.92))
    assert rows[0][0].startswith("TABLE 1.1")
    assert ["2023", "2024", "2025e", "2026f", "2027f", "2028f", "2026f", "2027f"] in rows
    assert ["World", "2.8", "2.9", "2.9", "2.5", "2.8", "2.8", "-0.1", "0.1"] in rows


def test_rows_to_markdown_pads_short_rows_and_escapes_pipes():
    assert rows_to_markdown([["Mode", "a|b"], ["Off"]]) == "| Mode | a\\|b |\n|---|---|\n| Off |  |"


def lines_count(text):
    return text.count("\n") + 1


def test_split_table_repeats_the_header_in_every_piece():
    rows = [["Mode", "230V"]] + [[f"row{i}", "1W"] for i in range(5)]
    pieces = split_table(rows, header_rows=1, max_tokens=4, count=lines_count)
    assert len(pieces) == 3
    assert all(piece.startswith("| Mode | 230V |\n|---|---|") for piece in pieces)
    assert "row4" in pieces[-1]


def test_split_table_never_splits_a_row_even_when_it_is_too_big():
    rows = [["Mode", "230V"]] + [[f"row{i}", "1W"] for i in range(3)]
    assert len(split_table(rows, header_rows=1, max_tokens=1, count=lines_count)) == 3


def test_remove_region_drops_only_lines_inside_the_box():
    page = make_page(1, [make_line("caption", y0=90), make_line("cell", y0=200), make_line("after", y0=400)])
    assert [l.text for l in remove_region(page, (0, 150, 600, 300)).lines] == ["caption", "after"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_tables.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'parse.tables'`

- [ ] **Step 3: Write `ingest/parse/tables.py`**

```python
"""Rebuild table rows from span positions inside a region chosen by the profile.

PyMuPDF's find_tables() was rejected after testing on these PDFs (spec §5.1 step 3).
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Callable

from model import BBox, Page, Span


def _center(bbox: BBox) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def _inside(bbox: BBox, region: BBox) -> bool:
    x, y = _center(bbox)
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def region_rows(page: Page, bbox: BBox, row_tolerance: float = 2.0, cell_gap: float = 6.0) -> list[list[str]]:
    spans = [s for line in page.lines for s in line.spans if s.text.strip() and _inside(s.bbox, bbox)]
    spans.sort(key=lambda s: (_center(s.bbox)[1], s.bbox[0]))
    rows: list[list[Span]] = []
    row_y: float | None = None
    for span in spans:
        y = _center(span.bbox)[1]
        if row_y is not None and abs(y - row_y) <= row_tolerance:
            rows[-1].append(span)
        else:
            rows.append([span])
            row_y = y
    return [_cells(row, cell_gap) for row in rows]


def _cells(row: list[Span], cell_gap: float) -> list[str]:
    cells: list[str] = []
    previous: Span | None = None
    for span in sorted(row, key=lambda s: s.bbox[0]):
        text = span.text.strip()
        gap = span.bbox[0] - previous.bbox[2] if previous else None
        if previous is not None and gap <= cell_gap:
            touching = gap <= 0.5 and span.size == previous.size and span.flags == previous.flags
            cells[-1] += ("" if touching else " ") + text
        else:
            cells.append(text)
        previous = span
    return [re.sub(r"\s+", " ", cell) for cell in cells]


def rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]

    def line(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"

    return "\n".join([line(padded[0]), "|" + "---|" * width] + [line(row) for row in padded[1:]])


def split_table(rows: list[list[str]], header_rows: int, max_tokens: int, count: Callable[[str], int]) -> list[str]:
    header, body = rows[:header_rows], rows[header_rows:]
    groups: list[list[list[str]]] = []
    current: list[list[str]] = []
    for row in body:
        if current and count(rows_to_markdown(header + current + [row])) > max_tokens:
            groups.append(current)
            current = []
        current.append(row)
    if current or not groups:
        groups.append(current)
    return [rows_to_markdown(header + group) for group in groups]


def remove_region(page: Page, bbox: BBox) -> Page:
    return replace(page, lines=tuple(line for line in page.lines if not _inside(line.bbox, bbox)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_tables.py -q -p no:warnings`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/parse/tables.py ingest/tests/test_tables.py
git commit -m "Rebuild table rows from span positions"
```

---

### Task 5: Paragraphs, packing and embed text

**Files:**
- Create: `ingest/chunk/__init__.py` (empty)
- Create: `ingest/chunk/common.py`
- Test: `ingest/tests/test_chunk_common.py`

**Interfaces:**
- Consumes: `model.Page`, `model.Line`; `parse.boilerplate` (test only); Task 1 fixtures/helpers.
- Produces (`chunk.common`):
  - `count_tokens(text: str) -> int` (tiktoken `cl100k_base`)
  - `split_sentences(text: str) -> list[str]`
  - `Paragraph(text: str, page: int, refs: tuple[int, ...] = ())` frozen
  - `paragraphs_from_pages(pages: list[Page]) -> list[Paragraph]`
  - `Packed(text: str, page_start: int, page_end: int, refs: tuple[int, ...])` frozen
  - `pack(paragraphs, target_tokens: int, overlap_tokens: int, count=count_tokens) -> list[Packed]`
  - `build_embed_text(doc_title, section_path, page_start, page_end, kind, content) -> str`

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_chunk_common.py`:
```python
from chunk.common import Paragraph, build_embed_text, count_tokens, pack, paragraphs_from_pages, split_sentences
from helpers import make_line, make_page
from parse.boilerplate import find_boilerplate, strip_boilerplate


def words(text):
    return len(text.split())


def test_count_tokens_uses_cl100k():
    assert count_tokens("hello world") == 2


def test_split_sentences():
    assert split_sentences("GPO grew. It printed 5 titles! Done? e.g. lower case stays") == [
        "GPO grew.",
        "It printed 5 titles!",
        "Done? e.g. lower case stays",
    ]


def test_lines_in_one_block_join_and_hyphenation_is_repaired():
    page = make_page(3, [make_line("Caps on prices limit pass-through to house-", y0=100), make_line("holds this year.", y0=112)])
    assert paragraphs_from_pages([page]) == [Paragraph("Caps on prices limit pass-through to households this year.", 3)]


def test_block_change_size_change_and_gap_start_new_paragraphs():
    page = make_page(
        1,
        [
            make_line("First block", y0=100, block=0),
            make_line("Second block", y0=112, block=1),
            make_line("Bigger heading", y0=124, block=1, size=14.0),
            make_line("Far below", y0=300, block=1, size=14.0),
        ],
    )
    assert [p.text for p in paragraphs_from_pages([page])] == ["First block", "Second block", "Bigger heading", "Far below"]


def test_paragraph_refs_are_unioned_in_order():
    page = make_page(7, [make_line("iMac uses 58 percent less", y0=100, refs=(18,)), make_line("energy.", y0=112, refs=(18, 3))])
    assert paragraphs_from_pages([page])[0].refs == (18, 3)


def test_gpo_fact_stays_in_one_paragraph(gpo_pages):
    pages = strip_boilerplate(gpo_pages, find_boilerplate(gpo_pages))
    texts = [p.text for p in paragraphs_from_pages(pages)]
    assert any("GPO staff visited 166 depository libraries in 25 states" in t for t in texts)


ONE, TWO, THREE = "one one one one", "two two two two", "three three three three"


def test_short_paragraphs_are_packed_up_to_the_target():
    chunks = pack([Paragraph(ONE, 1), Paragraph(TWO, 2), Paragraph(THREE, 3)], 8, 0, count=words)
    assert [(c.text, c.page_start, c.page_end) for c in chunks] == [(f"{ONE}\n\n{TWO}", 1, 2), (THREE, 3, 3)]


def test_overlap_repeats_the_trailing_paragraph():
    chunks = pack([Paragraph(ONE, 1), Paragraph(TWO, 2), Paragraph(THREE, 3)], 8, 4, count=words)
    assert [c.text for c in chunks] == [f"{ONE}\n\n{TWO}", f"{TWO}\n\n{THREE}"]


def test_overlap_is_dropped_when_it_would_overflow():
    long = "seven words in this one long paragraph"
    chunks = pack([Paragraph(ONE, 1), Paragraph(long, 2)], 8, 4, count=words)
    assert [c.text for c in chunks] == [ONE, long]


def test_long_paragraph_splits_on_sentences():
    text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."
    assert [c.text for c in pack([Paragraph(text, 1)], 4, 0, count=words)] == [
        "Alpha beta gamma.",
        "Delta epsilon zeta.",
        "Eta theta iota.",
    ]


def test_sentence_without_breaks_is_hard_split():
    text = " ".join(f"w{i}" for i in range(10))
    assert [words(c.text) for c in pack([Paragraph(text, 1)], 4, 0, count=words)] == [4, 4, 2]


def test_chunk_refs_union_paragraph_refs():
    chunks = pack([Paragraph(ONE, 1, (18,)), Paragraph(TWO, 1, (7, 18))], 8, 0, count=words)
    assert chunks[0].refs == (18, 7)


def test_embed_text_header_with_section_and_page_range():
    text = build_embed_text("GEP June 2026", "Ch. 2 › South Asia", 95, 96, "text", "Growth is projected")
    assert text == "GEP June 2026 › Ch. 2 › South Asia | p.95-96 | text\n\nGrowth is projected"


def test_embed_text_header_without_section_path():
    assert build_embed_text("iMac", "", 7, 7, "footnote", "x") == "iMac | p.7 | footnote\n\nx"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_chunk_common.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunk.common'`

- [ ] **Step 3: Write `ingest/chunk/__init__.py` (empty) and `ingest/chunk/common.py`**

```python
"""Shared chunking helpers: token counts, paragraphs from lines, token-bounded packing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

import tiktoken

from model import Page


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_BREAK.split(text.strip()) if s]


@dataclass(frozen=True)
class Paragraph:
    text: str
    page: int
    refs: tuple[int, ...] = ()


def paragraphs_from_pages(pages: list[Page]) -> list[Paragraph]:
    """A new paragraph starts on a new block, a font-size change, or a vertical gap."""
    out: list[Paragraph] = []
    for page in pages:
        text, refs, previous = None, [], None
        for line in page.lines:
            if not line.text:
                continue
            starts_new = (
                previous is None
                or line.block != previous.block
                or abs(line.size - previous.size) > 0.5
                or line.bbox[1] - previous.bbox[3] > 0.8 * line.size
            )
            if starts_new:
                if text is not None:
                    out.append(Paragraph(text, page.number, tuple(refs)))
                text, refs = line.text, list(line.refs)
            else:
                if text.endswith("-") and line.text[:1].islower():
                    text = text[:-1] + line.text
                else:
                    text = f"{text} {line.text}"
                refs += [r for r in line.refs if r not in refs]
            previous = line
        if text is not None:
            out.append(Paragraph(text, page.number, tuple(refs)))
    return out


@dataclass(frozen=True)
class Packed:
    text: str
    page_start: int
    page_end: int
    refs: tuple[int, ...]


@dataclass(frozen=True)
class _Unit:
    paragraph: int
    text: str
    page: int
    refs: tuple[int, ...]
    tokens: int


def _hard_split(text: str, target: int, count: Callable[[str], int]) -> list[str]:
    pieces, current = [], []
    for word in text.split():
        if current and count(" ".join(current + [word])) > target:
            pieces.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _units(paragraphs: list[Paragraph], target: int, count: Callable[[str], int]) -> list[_Unit]:
    units = []
    for index, paragraph in enumerate(paragraphs):
        tokens = count(paragraph.text)
        if tokens <= target:
            units.append(_Unit(index, paragraph.text, paragraph.page, paragraph.refs, tokens))
            continue
        for sentence in split_sentences(paragraph.text):
            pieces = [sentence] if count(sentence) <= target else _hard_split(sentence, target, count)
            for piece in pieces:
                units.append(_Unit(index, piece, paragraph.page, paragraph.refs, count(piece)))
    return units


def _join(units: list[_Unit]) -> Packed:
    text = units[0].text
    for previous, unit in zip(units, units[1:]):
        text += (" " if unit.paragraph == previous.paragraph else "\n\n") + unit.text
    refs: list[int] = []
    for unit in units:
        refs += [r for r in unit.refs if r not in refs]
    return Packed(text, min(u.page for u in units), max(u.page for u in units), tuple(refs))


def pack(
    paragraphs: list[Paragraph], target_tokens: int, overlap_tokens: int, count: Callable[[str], int] = count_tokens
) -> list[Packed]:
    chunks: list[Packed] = []
    current: list[_Unit] = []
    current_tokens = 0
    for unit in _units(paragraphs, target_tokens, count):
        if current and current_tokens + unit.tokens > target_tokens:
            chunks.append(_join(current))
            tail: list[_Unit] = []
            tail_tokens = 0
            for previous in reversed(current):
                if tail_tokens + previous.tokens > overlap_tokens:
                    break
                tail.insert(0, previous)
                tail_tokens += previous.tokens
            if len(tail) == len(current) or tail_tokens + unit.tokens > target_tokens:
                tail, tail_tokens = [], 0
            current, current_tokens = tail, tail_tokens
        current.append(unit)
        current_tokens += unit.tokens
    if current:
        chunks.append(_join(current))
    return chunks


def build_embed_text(doc_title: str, section_path: str, page_start: int, page_end: int, kind: str, content: str) -> str:
    pages = f"p.{page_start}" if page_start == page_end else f"p.{page_start}-{page_end}"
    where = f"{doc_title} › {section_path}" if section_path else doc_title
    return f"{where} | {pages} | {kind}\n\n{content}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_chunk_common.py -q -p no:warnings`
Expected: 14 passed (first run downloads the ~1.7 MB tiktoken encoding)

- [ ] **Step 5: Commit**

```bash
git add ingest/chunk/__init__.py ingest/chunk/common.py ingest/tests/test_chunk_common.py
git commit -m "Add paragraph packing helpers"
```

---

### Task 6: Disk cache and cached embedding

**Files:**
- Create: `ingest/cache.py`
- Create: `ingest/embed.py`
- Test: `ingest/tests/test_cache_embed.py`

**Interfaces:**
- Consumes: `config.REPO_ROOT`; an embedder with `.embed(texts, task_type) -> list[list[float]]` (`gemini.Embedder`, sub-project 1).
- Produces:
  - `cache.CACHE_ROOT: Path` (= `REPO_ROOT / "ingest" / ".cache"`)
  - `cache.JsonCache(root: Path, namespace: str)` with `.get(key) -> Any | None`, `.put(key, value) -> None`, `.clear() -> int`, staticmethod `.key(*parts: str) -> str`
  - `embed.embed_texts(texts: list[str], embedder, cache: JsonCache, model: str, dim: int) -> list[list[float]]`

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_cache_embed.py`:
```python
from cache import JsonCache
from embed import embed_texts


def test_put_get_roundtrip_and_missing_key(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    assert cache.get("nope") is None
    cache.put("k1", [0.6, 0.8])
    assert JsonCache(tmp_path, "imac/embeddings").get("k1") == [0.6, 0.8]


def test_clear_removes_only_its_namespace(tmp_path):
    imac, gep = JsonCache(tmp_path, "imac"), JsonCache(tmp_path, "gep")
    JsonCache(tmp_path, "imac/embeddings").put("a", 1)
    JsonCache(tmp_path, "imac/vision").put("b", 2)
    gep.put("c", 3)
    assert imac.clear() == 2
    assert JsonCache(tmp_path, "imac/embeddings").get("a") is None
    assert gep.get("c") == 3


def test_key_is_stable_and_order_sensitive():
    assert JsonCache.key("m", "768", "text") == JsonCache.key("m", "768", "text")
    assert JsonCache.key("m", "768", "text") != JsonCache.key("768", "m", "text")


class CountingEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts, task_type):
        self.calls.append((list(texts), task_type))
        return [[float(len(t)), 1.0] for t in texts]


def test_only_cache_misses_are_embedded_and_order_is_kept(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    embedder = CountingEmbedder()
    embed_texts(["bb"], embedder, cache, "m", 2)
    vectors = embed_texts(["a", "bb", "ccc"], embedder, cache, "m", 2)
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    assert embedder.calls == [(["bb"], "RETRIEVAL_DOCUMENT"), (["a", "ccc"], "RETRIEVAL_DOCUMENT")]


def test_second_run_makes_no_api_calls(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    embedder = CountingEmbedder()
    embed_texts(["a", "b"], embedder, cache, "m", 2)
    embed_texts(["a", "b"], embedder, cache, "m", 2)
    assert len(embedder.calls) == 1


def test_changing_model_or_dim_re_embeds(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    embedder = CountingEmbedder()
    embed_texts(["a"], embedder, cache, "m", 2)
    embed_texts(["a"], embedder, cache, "other-model", 2)
    embed_texts(["a"], embedder, cache, "m", 3)
    assert len(embedder.calls) == 3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_cache_embed.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'cache'`

- [ ] **Step 3: Write `ingest/cache.py`**

```python
"""Content-addressed JSON cache under ingest/.cache/<namespace>/ (git-ignored)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from config import REPO_ROOT

CACHE_ROOT = REPO_ROOT / "ingest" / ".cache"


class JsonCache:
    def __init__(self, root: Path, namespace: str):
        self.dir = Path(root) / namespace

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def put(self, key: str, value: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written entry

    def clear(self) -> int:
        if not self.dir.exists():
            return 0
        removed = sum(1 for p in self.dir.rglob("*.json"))
        shutil.rmtree(self.dir)
        return removed
```

- [ ] **Step 4: Write `ingest/embed.py`**

```python
"""Embed chunk texts through the disk cache so re-runs cost nothing."""
from __future__ import annotations

from cache import JsonCache


def embed_texts(texts: list[str], embedder, cache: JsonCache, model: str, dim: int) -> list[list[float]]:
    keys = [JsonCache.key(model, str(dim), text) for text in texts]
    missing = [i for i, key in enumerate(keys) if cache.get(key) is None]
    if missing:
        vectors = embedder.embed([texts[i] for i in missing], "RETRIEVAL_DOCUMENT")
        for i, vector in zip(missing, vectors):
            cache.put(keys[i], vector)
    return [cache.get(key) for key in keys]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_cache_embed.py -q -p no:warnings`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add ingest/cache.py ingest/embed.py ingest/tests/test_cache_embed.py
git commit -m "Cache embeddings on disk"
```

---

### Task 7: Transactional store

**Files:**
- Create: `ingest/store.py`
- Test: `ingest/tests/test_store.py`

**Interfaces:**
- Consumes: `db.connect`, `config.load_settings` (sub-project 1); `model.SectionDraft`, `model.ChunkDraft`.
- Produces:
  - `store.DocumentMeta(slug: str, title: str, profile: str, pages: int)` frozen
  - `store.StoreStats(sections: int, chunks: int)` frozen
  - `store.MixedEmbeddingError(RuntimeError)`
  - `store.store_document(conn, meta, sections, chunks, embed_texts: list[str], vectors: list[list[float]], embed_model: str, embed_dim: int) -> StoreStats` — deletes the slug's old rows and writes the new ones in one transaction; commits on success, rolls back and re-raises on any error

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_store.py`:
```python
import pytest

from config import load_settings
from db import connect
from model import ChunkDraft, SectionDraft
from store import DocumentMeta, MixedEmbeddingError, store_document

pytestmark = pytest.mark.integration


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def conn(settings):
    connection = connect(settings)
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    yield connection
    connection.rollback()
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    connection.close()


def unit_vector(dim, hot=0):
    vec = [0.0] * dim
    vec[hot] = 1.0
    return vec


def sample(dim):
    sections = [
        SectionDraft("ch2/sar", "South Asia", 2, "Ch. 2 › South Asia", 95, 98, parent_key="ch2", kind="region"),
        SectionDraft("ch2", "Ch. 2 Regional Outlooks", 1, "Ch. 2", 69, 106, parent_key="root"),
        SectionDraft("root", "Test doc", 0, "", 1, 200),
    ]
    chunks = [
        ChunkDraft("ch2/sar", "text", "Growth in SAR is projected to slow.", 95, 95, footnotes=[{"n": 1, "text": "Fiscal years."}]),
        ChunkDraft("ch2/sar", "table", "| Country | 2026f |", 96, 96),
        ChunkDraft("ch2", "text", "Regional overview.", 69, 69),
    ]
    texts = [f"embed {c.content}" for c in chunks]
    return sections, chunks, texts, [unit_vector(dim, i) for i in range(len(chunks))]


def store(conn, settings, slug="test-store", **overrides):
    sections, chunks, texts, vectors = sample(settings.embed_dim)
    args = dict(sections=sections, chunks=chunks, embed_texts=texts, vectors=vectors)
    args.update(overrides)
    meta = DocumentMeta(slug, "Test doc", "plain", 200)
    return store_document(conn, meta, embed_model=settings.embed_model, embed_dim=settings.embed_dim, **args)


def test_stores_sections_with_parents_and_chunks_with_seq(conn, settings):
    stats = store(conn, settings)
    assert (stats.sections, stats.chunks) == (3, 3)
    rows = conn.execute(
        "SELECT s.path, p.title, c.seq, c.kind, c.footnotes, c.embed_text "
        "FROM chunks c JOIN sections s ON s.id = c.section_id LEFT JOIN sections p ON p.id = s.parent_id "
        "JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-store' ORDER BY c.id"
    ).fetchall()
    assert rows == [
        ("Ch. 2 › South Asia", "Ch. 2 Regional Outlooks", 0, "text", [{"n": 1, "text": "Fiscal years."}], "embed Growth in SAR is projected to slow."),
        ("Ch. 2 › South Asia", "Ch. 2 Regional Outlooks", 1, "table", [], "embed | Country | 2026f |"),
        ("Ch. 2", "Test doc", 0, "text", [], "embed Regional overview."),
    ]


def test_restoring_replaces_instead_of_duplicating(conn, settings):
    store(conn, settings)
    store(conn, settings)
    assert conn.execute("SELECT count(*) FROM documents WHERE slug = 'test-store'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-store'"
    ).fetchone()[0] == 3


def test_failed_store_keeps_the_previous_version(conn, settings):
    store(conn, settings)
    sections, chunks, texts, vectors = sample(settings.embed_dim)
    chunks[0].section_key = "missing-section"
    with pytest.raises(KeyError):
        store(conn, settings, chunks=chunks)
    assert conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-store'"
    ).fetchone()[0] == 3


def test_refuses_to_mix_embedding_models(conn, settings):
    conn.execute(
        "INSERT INTO documents (slug, title, profile, pages, embed_model, embed_dim) "
        "VALUES ('test-other', 'Other', 'plain', 1, 'some-other-model', %s)",
        (settings.embed_dim,),
    )
    conn.commit()
    with pytest.raises(MixedEmbeddingError, match="test-other"):
        store(conn, settings)


def test_vector_count_mismatch_is_rejected_before_writing(conn, settings):
    with pytest.raises(ValueError, match="3 chunks but 2 vectors"):
        store(conn, settings, vectors=[unit_vector(settings.embed_dim)] * 2)
    assert conn.execute("SELECT count(*) FROM documents WHERE slug = 'test-store'").fetchone()[0] == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_store.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'store'`

- [ ] **Step 3: Write `ingest/store.py`**

```python
"""Write one document's sections and chunks in a single transaction (spec §5.1 step 7)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from model import ChunkDraft, SectionDraft


@dataclass(frozen=True)
class DocumentMeta:
    slug: str
    title: str
    profile: str
    pages: int


@dataclass(frozen=True)
class StoreStats:
    sections: int
    chunks: int


class MixedEmbeddingError(RuntimeError):
    pass


def store_document(
    conn: psycopg.Connection,
    meta: DocumentMeta,
    sections: list[SectionDraft],
    chunks: list[ChunkDraft],
    embed_texts: list[str],
    vectors: list[list[float]],
    embed_model: str,
    embed_dim: int,
) -> StoreStats:
    if not (len(chunks) == len(embed_texts) == len(vectors)):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors and {len(embed_texts)} embed texts")
    try:
        others = conn.execute(
            "SELECT slug, embed_model, embed_dim FROM documents WHERE slug <> %s", (meta.slug,)
        ).fetchall()
        mixed = [slug for slug, model, dim in others if (model, dim) != (embed_model, embed_dim)]
        if mixed:
            raise MixedEmbeddingError(
                f"documents {', '.join(mixed)} use a different embedding model/dim than "
                f"{embed_model}/{embed_dim}; re-ingest them or fix .env"
            )
        conn.execute("DELETE FROM documents WHERE slug = %s", (meta.slug,))
        doc_id = conn.execute(
            "INSERT INTO documents (slug, title, profile, pages, embed_model, embed_dim) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (meta.slug, meta.title, meta.profile, meta.pages, embed_model, embed_dim),
        ).fetchone()[0]
        section_ids: dict[str, int] = {}
        for section in sorted(sections, key=lambda s: s.level):  # parents before children
            parent_id = section_ids[section.parent_key] if section.parent_key else None
            section_ids[section.key] = conn.execute(
                "INSERT INTO sections (doc_id, parent_id, title, level, path, kind, page_start, page_end, content) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (doc_id, parent_id, section.title, section.level, section.path, section.kind,
                 section.page_start, section.page_end, section.content),
            ).fetchone()[0]
        seq: dict[str, int] = defaultdict(int)
        rows = []
        for chunk, text, vector in zip(chunks, embed_texts, vectors):
            rows.append(
                (doc_id, section_ids[chunk.section_key], seq[chunk.section_key], chunk.kind, chunk.content, text,
                 chunk.page_start, chunk.page_end, Jsonb(chunk.footnotes), Jsonb(chunk.metadata),
                 np.asarray(vector, dtype=np.float32))
            )
            seq[chunk.section_key] += 1
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (doc_id, section_id, seq, kind, content, embed_text, page_start, page_end, "
                "footnotes, metadata, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return StoreStats(len(sections), len(chunks))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_store.py -q -p no:warnings`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add ingest/store.py ingest/tests/test_store.py
git commit -m "Store documents in one transaction"
```

---

### Task 8: Pipeline, plain profile and report

**Files:**
- Create: `ingest/report.py`
- Create: `ingest/pipeline.py`
- Create: `ingest/chunk/plain.py`
- Create: `ingest/profiles.py`
- Test: `ingest/tests/test_pipeline.py`
- Test: `ingest/tests/test_pipeline_db.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7; `config.Settings`, `config.REPO_ROOT`.
- Produces:
  - `report.Report(slug: str, title: str)` dataclass with fields `pages=0`, `sections=0`, `notes=0`, `boilerplate=0`, `chunks_by_kind: Counter`, `unresolved: list[tuple[int, int]]` (page, n), `warnings: list[str]`; method `format() -> str`
  - `pipeline.EMBED_TOKEN_LIMIT = 2000`
  - `pipeline.DocSpec(slug, title, path: Path, profile: str, skip_pages: frozenset[int] = frozenset(), endnote_pages: frozenset[int] = frozenset())` frozen
  - `pipeline.Prepared(pages: list[Page], body_size: float, notes: NoteIndex, page_count: int, boilerplate: int)` dataclass
  - `pipeline.BuildResult(sections: list[SectionDraft], chunks: list[ChunkDraft], warnings: list[str] = [])` dataclass
  - `pipeline.Profile = Callable[[DocSpec, Prepared], BuildResult]`
  - `pipeline.prepare(spec: DocSpec) -> Prepared`
  - `pipeline.section_for_page(sections, page: int) -> SectionDraft` (deepest section containing the page)
  - `pipeline.note_chunks(notes: list[Note], sections) -> list[ChunkDraft]` (`content = f"Footnote {n}: {text}"`)
  - `pipeline.attach_footnotes(chunks, index: NoteIndex, report: Report) -> None`
  - `pipeline.check_token_limit(texts: list[str], chunks: list[ChunkDraft]) -> None` (raises `ValueError`)
  - `pipeline.run(spec, profile, settings, conn, embedder, cache_root: Path) -> Report`
  - `chunk.plain.build(spec: DocSpec, prepared: Prepared) -> BuildResult` (one root section, 500/75 packing)
  - `profiles.DOCS: dict[str, DocSpec]` (gpo, gep, imac — all `profile="plain"` until sub-projects 3–5), `profiles.PROFILES: dict[str, Profile]` (`{"plain": chunk.plain.build}`)

- [ ] **Step 1: Write the failing unit tests**

`ingest/tests/test_pipeline.py`:
```python
from collections import Counter

import pytest

from helpers import make_line, make_page
from model import ChunkDraft, Note, SectionDraft
from parse.footnotes import NoteIndex
from pipeline import DocSpec, Prepared, attach_footnotes, check_token_limit, note_chunks, section_for_page
from chunk.plain import build
from report import Report

SECTIONS = [
    SectionDraft("root", "Doc", 0, "", 1, 200),
    SectionDraft("ch2", "Ch. 2", 1, "Ch. 2", 69, 106, parent_key="root"),
    SectionDraft("ch2/sar", "South Asia", 2, "Ch. 2 › South Asia", 95, 98, parent_key="ch2"),
]


def test_section_for_page_picks_the_deepest_match():
    assert section_for_page(SECTIONS, 96).key == "ch2/sar"
    assert section_for_page(SECTIONS, 70).key == "ch2"
    assert section_for_page(SECTIONS, 5).key == "root"


def test_note_chunks_are_footnote_kind_in_the_notes_section():
    [chunk] = note_chunks([Note(1, 96, "Fiscal years.")], SECTIONS)
    assert (chunk.section_key, chunk.kind, chunk.content, chunk.page_start) == ("ch2/sar", "footnote", "Footnote 1: Fiscal years.", 96)


def test_attach_footnotes_resolves_and_reports_unresolved():
    chunks = [ChunkDraft("root", "text", "58 percent less energy", 7, 7, refs=(18, 4))]
    report = Report("imac", "iMac")
    attach_footnotes(chunks, NoteIndex([Note(18, 12, "ENERGY STAR rules.")], frozenset({12})), report)
    assert chunks[0].footnotes == [{"n": 18, "text": "ENERGY STAR rules."}]
    assert report.unresolved == [(7, 4)]


def test_token_limit_fails_before_embedding():
    chunk = ChunkDraft("root", "table", "x", 186, 186)
    with pytest.raises(ValueError, match="p.186"):
        check_token_limit(["word " * 2100], [chunk])
    check_token_limit(["short"], [chunk])  # no error


def test_report_format_lists_counts_and_unresolved():
    report = Report("imac", "iMac report")
    report.pages, report.sections, report.notes = 12, 1, 19
    report.chunks_by_kind = Counter({"text": 15, "footnote": 19})
    report.unresolved = [(3, 12)]
    text = report.format()
    assert "imac — iMac report" in text
    assert "footnote 19, text 15 (total 34)" in text
    assert "p3:12" in text


def test_plain_profile_handles_empty_pages_and_no_notes(tmp_path):
    pages = [
        make_page(1, []),  # photo-only page
        make_page(2, [make_line("GPO distributed 802,834 copies of 4,211 titles.")]),
        make_page(3, [make_line("Second page text.")]),
    ]
    spec = DocSpec("gpo", "GPO 2018 Annual Report", tmp_path / "x.pdf", "plain")
    result = build(spec, Prepared(pages, 10.0, NoteIndex([]), 3, 0))
    [root] = result.sections
    assert (root.key, root.level, root.page_start, root.page_end) == ("root", 0, 1, 3)
    assert [c.content for c in result.chunks] == ["GPO distributed 802,834 copies of 4,211 titles.\n\nSecond page text."]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_pipeline.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 3: Write `ingest/report.py`**

```python
"""Per-document ingestion summary printed at the end of a run (spec §5.1 step 8)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Report:
    slug: str
    title: str
    pages: int = 0
    sections: int = 0
    notes: int = 0
    boilerplate: int = 0
    chunks_by_kind: Counter = field(default_factory=Counter)
    unresolved: list[tuple[int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def format(self) -> str:
        kinds = ", ".join(f"{kind} {n}" for kind, n in sorted(self.chunks_by_kind.items()))
        total = sum(self.chunks_by_kind.values())
        unresolved = ", ".join(f"p{page}:{n}" for page, n in self.unresolved) or "none"
        lines = [
            f"{self.slug} — {self.title}",
            f"  pages {self.pages} · sections {self.sections} · notes {self.notes} · boilerplate patterns {self.boilerplate}",
            f"  chunks: {kinds} (total {total})",
            f"  unresolved footnote markers: {unresolved}",
        ]
        lines += [f"  warning: {w}" for w in self.warnings]
        return "\n".join(lines)
```

- [ ] **Step 4: Write `ingest/pipeline.py`**

```python
"""Shared ingestion steps around a document profile (spec §5.1)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cache import JsonCache
from chunk.common import build_embed_text, count_tokens
from config import Settings
from embed import embed_texts
from model import ChunkDraft, Note, Page, SectionDraft
from parse.boilerplate import find_boilerplate, strip_boilerplate
from parse.footnotes import NoteIndex, split_notes, strip_markers
from parse.pdf_text import body_font_size, load_pages
from report import Report
from store import DocumentMeta, store_document

EMBED_TOKEN_LIMIT = 2000  # Gemini embedding input limit is 2048 tokens; cl100k is a close proxy


@dataclass(frozen=True)
class DocSpec:
    slug: str
    title: str
    path: Path
    profile: str
    skip_pages: frozenset[int] = frozenset()
    endnote_pages: frozenset[int] = frozenset()


@dataclass
class Prepared:
    pages: list[Page]
    body_size: float
    notes: NoteIndex
    page_count: int
    boilerplate: int


@dataclass
class BuildResult:
    sections: list[SectionDraft]
    chunks: list[ChunkDraft]
    warnings: list[str] = field(default_factory=list)


Profile = Callable[[DocSpec, Prepared], BuildResult]


def prepare(spec: DocSpec) -> Prepared:
    pages = load_pages(spec.path)
    body = body_font_size(pages)
    keys = find_boilerplate(pages)
    kept = [p for p in strip_boilerplate(pages, keys) if p.number not in spec.skip_pages]
    notes: list[Note] = []
    clean: list[Page] = []
    for page in kept:
        page_notes, rest = split_notes(page, body)
        notes += page_notes
        clean.append(strip_markers(rest))
    return Prepared(clean, body, NoteIndex(notes, spec.endnote_pages), len(pages), len(keys))


def section_for_page(sections: list[SectionDraft], page: int) -> SectionDraft:
    containing = [s for s in sections if s.page_start <= page <= s.page_end]
    return max(containing, key=lambda s: s.level) if containing else min(sections, key=lambda s: s.level)


def note_chunks(notes: list[Note], sections: list[SectionDraft]) -> list[ChunkDraft]:
    return [
        ChunkDraft(section_for_page(sections, note.page).key, "footnote", f"Footnote {note.n}: {note.text}", note.page, note.page)
        for note in notes
    ]


def attach_footnotes(chunks: list[ChunkDraft], index: NoteIndex, report: Report) -> None:
    for chunk in chunks:
        for n in chunk.refs:
            note = index.resolve(n, chunk.page_start)
            if note is None:
                report.unresolved.append((chunk.page_start, n))
            else:
                chunk.footnotes.append({"n": n, "text": note.text})


def check_token_limit(texts: list[str], chunks: list[ChunkDraft]) -> None:
    too_big = [f"p.{c.page_start} {c.kind} ({count_tokens(t)} tokens)" for t, c in zip(texts, chunks) if count_tokens(t) > EMBED_TOKEN_LIMIT]
    if too_big:
        raise ValueError(f"chunks over the {EMBED_TOKEN_LIMIT}-token embedding limit: {'; '.join(too_big)}")


def run(spec: DocSpec, profile: Profile, settings: Settings, conn, embedder, cache_root: Path) -> Report:
    report = Report(spec.slug, spec.title)
    prepared = prepare(spec)
    result = profile(spec, prepared)
    chunks = result.chunks + note_chunks(prepared.notes.notes, result.sections)
    attach_footnotes(chunks, prepared.notes, report)
    paths = {s.key: s.path for s in result.sections}
    texts = [build_embed_text(spec.title, paths[c.section_key], c.page_start, c.page_end, c.kind, c.content) for c in chunks]
    check_token_limit(texts, chunks)
    cache = JsonCache(cache_root, f"{spec.slug}/embeddings")
    vectors = embed_texts(texts, embedder, cache, settings.embed_model, settings.embed_dim)
    meta = DocumentMeta(spec.slug, spec.title, spec.profile, prepared.page_count)
    store_document(conn, meta, result.sections, chunks, texts, vectors, settings.embed_model, settings.embed_dim)
    report.pages = prepared.page_count
    report.sections = len(result.sections)
    report.notes = len(prepared.notes.notes)
    report.boilerplate = prepared.boilerplate
    report.chunks_by_kind = Counter(c.kind for c in chunks)
    report.warnings += result.warnings
    return report
```

- [ ] **Step 5: Write `ingest/chunk/plain.py`**

```python
"""Baseline profile: the whole document as one section, paragraphs packed into ~500-token chunks."""
from __future__ import annotations

from chunk.common import pack, paragraphs_from_pages
from model import ChunkDraft, SectionDraft
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 500
OVERLAP_TOKENS = 75


def build(spec: DocSpec, prepared: Prepared) -> BuildResult:
    numbers = [page.number for page in prepared.pages]
    root = SectionDraft("root", spec.title, 0, "", min(numbers), max(numbers))
    packed = pack(paragraphs_from_pages(prepared.pages), TARGET_TOKENS, OVERLAP_TOKENS)
    chunks = [ChunkDraft("root", "text", p.text, p.page_start, p.page_end, refs=p.refs) for p in packed]
    return BuildResult([root], chunks)
```

- [ ] **Step 6: Write `ingest/profiles.py`**

```python
"""The three source documents and the profile each one uses (sub-projects 3–5 swap in real profiles)."""
from __future__ import annotations

from chunk import plain
from config import REPO_ROOT
from pipeline import DocSpec, Profile

DOCS_DIR = REPO_ROOT / "docs"

DOCS: dict[str, DocSpec] = {
    "gpo": DocSpec("gpo", "GPO 2018 Annual Report", DOCS_DIR / "GPO-Anual-report.pdf", "plain"),
    "gep": DocSpec("gep", "Global Economic Prospects, June 2026", DOCS_DIR / "GEP-Jun-2026.pdf", "plain"),
    "imac": DocSpec(
        "imac",
        "iMac Product Environmental Report (Oct 2024)",
        DOCS_DIR / "iMac_PER_Oct2024.pdf",
        "plain",
        endnote_pages=frozenset({11, 12}),
    ),
}

PROFILES: dict[str, Profile] = {"plain": plain.build}
```

- [ ] **Step 7: Run the unit tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_pipeline.py -q -p no:warnings`
Expected: 6 passed

- [ ] **Step 8: Write the end-to-end DB test (fake embedder, real PDF and DB)**

`ingest/tests/test_pipeline_db.py`:
```python
from dataclasses import replace

import pytest

from chunk import plain
from config import load_settings
from db import connect
from pipeline import run
from profiles import DOCS

pytestmark = pytest.mark.integration


class FakeEmbedder:
    def __init__(self, dim):
        self.dim = dim

    def embed(self, texts, task_type):
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]


@pytest.fixture
def conn():
    connection = connect(load_settings())
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    yield connection
    connection.rollback()
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    connection.close()


def test_imac_ingests_end_to_end_with_footnotes(conn, tmp_path):
    settings = load_settings()
    spec = replace(DOCS["imac"], slug="test-imac")
    report = run(spec, plain.build, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path)

    assert report.notes == 19
    assert report.chunks_by_kind["footnote"] == 19
    assert report.unresolved == []
    stored = conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-imac'"
    ).fetchone()[0]
    assert stored == sum(report.chunks_by_kind.values())

    content, footnotes = conn.execute(
        "SELECT c.content, c.footnotes FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.slug = 'test-imac' AND c.kind = 'text' AND c.content LIKE %s",
        ("%iMac consumes 58 percent less energy%",),
    ).fetchone()
    assert "ENERGY STAR.18" not in content
    assert any(f["n"] == 18 and "ENERGY STAR Program Requirements" in f["text"] for f in footnotes)

    leftovers = conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.slug = 'test-imac' AND c.content LIKE %s",
        ("%iMac | Product Environmental Report%",),
    ).fetchone()[0]
    assert leftovers == 0
```

- [ ] **Step 9: Run it**

Run: `cd ingest && uv run pytest tests/test_pipeline_db.py -q -p no:warnings`
Expected: 1 passed

- [ ] **Step 10: Commit**

```bash
git add ingest/report.py ingest/pipeline.py ingest/chunk/plain.py ingest/profiles.py ingest/tests/test_pipeline.py ingest/tests/test_pipeline_db.py
git commit -m "Add ingestion pipeline and plain profile"
```

---

### Task 9: CLI, post-ingest validation and live run

**Files:**
- Create: `ingest/ingest.py`
- Create: `ingest/validate.py`
- Test: `ingest/tests/test_cli.py`
- Test: `ingest/tests/test_validate.py`
- Modify: `README.md` (Ingestion section)

**Interfaces:**
- Consumes: `profiles.DOCS`, `profiles.PROFILES`, `pipeline.run`, `cache.CACHE_ROOT`, `cache.JsonCache`, `config.load_settings`, `db.connect`, `gemini.make_embedder`, `store.store_document` (test only).
- Produces:
  - `ingest.parse_args(argv: list[str] | None) -> argparse.Namespace` with `docs: list[str]` (default: all slugs in `DOCS`) and `reset: bool`
  - `ingest.main(argv=None) -> int`
  - `validate.CheckResult(name: str, ok: bool, detail: str)` dataclass
  - `validate.generic_checks(conn, settings, slug: str) -> list[CheckResult]`
  - `validate.DOC_CHECKS: dict[str, list[Callable[[psycopg.Connection], CheckResult]]]` — empty now; sub-projects 3–5 add per-document checks (spec §9)
  - `validate.main(argv=None) -> int`

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_cli.py`:
```python
import pytest

from ingest import parse_args


def test_defaults_to_all_documents():
    args = parse_args([])
    assert args.docs == ["gpo", "gep", "imac"]
    assert args.reset is False


def test_doc_can_be_repeated_and_unknown_docs_are_rejected():
    assert parse_args(["--doc", "imac", "--doc", "gpo", "--reset"]).docs == ["imac", "gpo"]
    with pytest.raises(SystemExit):
        parse_args(["--doc", "nope"])
```

`ingest/tests/test_validate.py`:
```python
import pytest

from config import load_settings
from db import connect
from model import ChunkDraft, SectionDraft
from store import DocumentMeta, store_document
from validate import generic_checks

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    settings = load_settings()
    connection = connect(settings)
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    store_document(
        connection,
        DocumentMeta("test-validate", "Validate", "plain", 1),
        [SectionDraft("root", "Validate", 0, "", 1, 1)],
        [ChunkDraft("root", "text", "Some content.", 1, 1)],
        ["Validate | p.1 | text\n\nSome content."],
        [vec],
        settings.embed_model,
        settings.embed_dim,
    )
    yield connection
    connection.rollback()
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    connection.close()


def test_good_document_passes_every_generic_check(conn):
    results = generic_checks(conn, load_settings(), "test-validate")
    assert results and all(r.ok for r in results), [r for r in results if not r.ok]


def test_empty_chunk_content_is_reported(conn):
    conn.execute(
        "UPDATE chunks SET content = '  ' WHERE doc_id = (SELECT id FROM documents WHERE slug = 'test-validate')"
    )
    conn.commit()
    failed = [r.name for r in generic_checks(conn, load_settings(), "test-validate") if not r.ok]
    assert failed == ["no empty chunks"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_cli.py tests/test_validate.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'parse_args' from 'ingest'` (or `ModuleNotFoundError: No module named 'validate'`)

- [ ] **Step 3: Write `ingest/ingest.py`**

```python
"""Ingest the source PDFs into Postgres.

Run from ingest/:  uv run python ingest.py [--doc gpo|gep|imac ...] [--reset]
"""
from __future__ import annotations

import argparse
import sys

from cache import CACHE_ROOT, JsonCache
from config import load_settings
from db import connect
from gemini import make_embedder
from pipeline import run
from profiles import DOCS, PROFILES


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest the source PDFs into Postgres.")
    parser.add_argument("--doc", dest="docs", action="append", choices=list(DOCS), help="document to ingest (repeatable; default: all)")
    parser.add_argument("--reset", action="store_true", help="drop this document's cached vision/summary/embedding results first")
    args = parser.parse_args(argv)
    args.docs = args.docs or list(DOCS)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    embedder = make_embedder(settings)
    with connect(settings) as conn:
        for slug in args.docs:
            spec = DOCS[slug]
            if args.reset:
                print(f"{slug}: cleared {JsonCache(CACHE_ROOT, slug).clear()} cache entries")
            report = run(spec, PROFILES[spec.profile], settings, conn, embedder, CACHE_ROOT)
            print(report.format())
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Write `ingest/validate.py`**

```python
"""Post-ingest assertions (spec §9). Run from ingest/:  uv run python validate.py [--doc slug ...]"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Callable

import psycopg

from config import Settings, load_settings
from db import connect


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


# Per-document checks are added by the profile sub-projects (3: gpo, 4: gep, 5: imac).
DOC_CHECKS: dict[str, list[Callable[[psycopg.Connection], CheckResult]]] = {}


def generic_checks(conn: psycopg.Connection, settings: Settings, slug: str) -> list[CheckResult]:
    doc = conn.execute("SELECT id, embed_model, embed_dim FROM documents WHERE slug = %s", (slug,)).fetchone()
    if doc is None:
        return [CheckResult("document stored", False, f"no document '{slug}'; run ingest.py --doc {slug}")]
    doc_id, model, dim = doc

    def count(where: str, *params) -> int:
        return conn.execute(f"SELECT count(*) FROM chunks WHERE doc_id = %s AND {where}", (doc_id, *params)).fetchone()[0]

    total = count("true")
    empty = count("btrim(content) = ''")
    wrong_dim = count("vector_dims(embedding) <> %s", settings.embed_dim)
    orphans = count("section_id IS NULL")
    same_model = (model, dim) == (settings.embed_model, settings.embed_dim)
    return [
        CheckResult("document stored", True, f"{total} chunks"),
        CheckResult("has chunks", total > 0, f"{total} chunks"),
        CheckResult("no empty chunks", empty == 0, f"{empty} empty"),
        CheckResult("embedding dims", wrong_dim == 0, f"{wrong_dim} chunks not {settings.embed_dim}-dim"),
        CheckResult("chunks have sections", orphans == 0, f"{orphans} without a section"),
        CheckResult("embedding model", same_model, f"stored {model}/{dim}, .env {settings.embed_model}/{settings.embed_dim}"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check ingested documents.")
    parser.add_argument("--doc", dest="docs", action="append", help="slug to check (default: every stored document)")
    args = parser.parse_args(argv)
    settings = load_settings()
    ok = True
    with connect(settings) as conn:
        slugs = args.docs or [row[0] for row in conn.execute("SELECT slug FROM documents ORDER BY slug")]
        for slug in slugs:
            results = generic_checks(conn, settings, slug) + [check(conn) for check in DOC_CHECKS.get(slug, [])]
            for r in results:
                print(f"{'✓' if r.ok else '✗'} {slug}: {r.name} — {r.detail}")
            ok = ok and all(r.ok for r in results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_cli.py tests/test_validate.py -q -p no:warnings`
Expected: 4 passed

- [ ] **Step 6: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 96 passed, 1 deselected (29 from sub-project 1 + 67 new)

- [ ] **Step 7: Live run on the iMac report (real Gemini embeddings)**

Run: `cd ingest && uv run python ingest.py --doc imac`
Expected: a report like
```
imac — iMac Product Environmental Report (Oct 2024)
  pages 12 · sections 1 · notes 19 · boilerplate patterns 2
  chunks: footnote 19, text <N> (total <N+19>)
  unresolved footnote markers: none
```

Run it again: `cd ingest && uv run python ingest.py --doc imac`
Expected: same report, finishes in a few seconds (embeddings come from `ingest/.cache/imac/embeddings`, no API calls).

Run: `cd ingest && uv run python validate.py --doc imac`
Expected: six `✓ imac:` lines, exit 0.

Run: `docker compose exec -T db psql -U rag -d rag -Atc "SELECT jsonb_path_query_first(footnotes, '$[*] ? (@.n == 18).text') FROM chunks WHERE content LIKE '%iMac consumes 58 percent less energy%' LIMIT 1"` (from the repo root)
Expected: a string starting `"Energy consumption and energy efficiency values are based on the ENERGY STAR Program Requirements`

- [ ] **Step 8: Update `README.md`**

Replace the code block under `## Ingestion (Python)` with:
````markdown
```bash
uv sync                          # install dependencies
uv run python doctor.py          # check DB + Gemini are reachable and agree with .env
uv run python ingest.py          # ingest all three PDFs (or: --doc imac, repeatable)
uv run python ingest.py --doc imac --reset   # re-run without cached embeddings/vision/summaries
uv run python validate.py        # post-ingest checks for every stored document
uv run pytest                    # unit + DB integration tests (DB must be running)
uv run pytest -m live            # tests that call the real Gemini API
```
````

- [ ] **Step 9: Commit**

```bash
git add ingest/ingest.py ingest/validate.py ingest/tests/test_cli.py ingest/tests/test_validate.py README.md
git commit -m "Add ingest CLI and validation"
```

---

## Sub-project acceptance (spec §10, row 2)

- [ ] `cd ingest && uv run pytest -q` → 96 passed, 1 deselected
- [ ] `uv run python ingest.py --doc imac` → report shows 19 notes, 19 footnote chunks, no unresolved markers; second run makes no embedding calls
- [ ] `uv run python validate.py --doc imac` → all ✓, exit 0
- [ ] `/api/health` (web) lists `imac` in `documents` with `ok: true`
- [ ] `git log --oneline main..HEAD` shows the spec update, the plan, and 9 short task commits with no trailers

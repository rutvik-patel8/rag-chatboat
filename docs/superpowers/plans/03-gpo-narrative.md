# GPO Narrative Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `plain` profile for the GPO 2018 Annual Report with a `narrative` profile that finds headings from font style, builds a two-level section tree, keeps photo captions and statement tables out of body text, and stores the three brief facts in the right sections.

**Architecture:** `Span` gains `font` and `color` so a profile can read typography. `chunk/narrative.py` classifies every line of the prepared pages as heading (level 1/2, standalone or run-in), caption, table-page row, or body. It then opens sections in reading order, packs body paragraphs per section (never across a heading), emits caption chunks and table chunks, and returns them to the shared pipeline. A `NarrativeLayout` dataclass holds the styles and page numbers observed in this PDF. `validate.py` gains GPO checks for the brief's facts.

**Tech Stack:** Python 3 + uv, PyMuPDF, existing ingestion core (sub-project 2), pytest, Docker Postgres.

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md` — this plan implements §10 row 3 using §5.2 (rewritten from probes in commit `912533e`) and §9.

## Global Constraints

- Run every command from `ingest/` with `uv run`. Docker DB must be up.
- Page numbers are 1-based PDF page indexes.
- GPO skipped pages: **1, 31, 32, 33**. Narrative pages are **≤ 30**; financial pages **≥ 34**.
- Narrative heading styles: level 1 = `HelveticaNeueLTStd-Md` **10.0pt** colour **`0x646E74`**; level 2 = `HelveticaNeueLTStd-Md` **9.5pt** colour **`0x507CBF`** (standalone or run-in). Caption style = `HelveticaNeueLTStd-Roman` **8.2pt**.
- Financial headings: level 1 = line size **≥ 20**; level 2 = bold face (font name contains `Bold`), size **11.5–14.5**, **< 90** chars, not numeric, not ending `:`, alone on its row, not repeated on **≥ 3** pages.
- Injected headings: p2 **"Letter of Transmittal"**, p7 **"About GPO"**; one-page section p14 **"GPO By the Numbers FY 2018"**. Table pages: **14, 58, 59, 60**.
- Chunk sizes: text **500/75** per section; tables split at **≤ 450** tokens with the header repeated.
- Section paths: level 1 = its title; level 2 = `"<L1 title> › <L2 title>"`.
- Commits: one per task, short imperative subject only (≤ 50 chars). **No `Co-Authored-By` trailer, no body.**

## Review Focus

1. **Body text in other faces on narrative pages** (dark-grey bold URLs, inline emphasis, teal chart titles) → stays body text and never opens a section. Pinned in Task 3.
2. **Page-top titles that PDF order places after body text** (p4 "A Message from the Acting Deputy Director") → the facts on that page land in that section, not the previous one. Pinned in Task 4.
3. **Titles split over several lines** (divider pages "SECTION I" + "MANAGEMENT…", wrapped statement titles) → one section each, not a string of empty sections. Pinned in Task 4.
4. **Bold labels that share a row with numbers** (Note 2 "Unrestricted", memo fields) and repeated title-block lines → not headings. Pinned in Task 3.
5. **Captions and statement tables** → never duplicated inside text chunks; table rows stay aligned (`$` merged with its value). Pinned in Task 4.

---

### Task 1: Record font and colour on spans

**Files:**
- Modify: `ingest/model.py` (`Span`)
- Modify: `ingest/parse/pdf_text.py` (`load_pages`)
- Test: `ingest/tests/test_pdf_text.py`

**Interfaces:**
- Consumes: sub-project 2 `model.Span`, `parse.pdf_text.load_pages`, fixture `gpo_pages`, helper `line_with`.
- Produces: `model.Span(text, size, flags, bbox, font: str = "", color: int = 0)`; `load_pages` fills `font` from PyMuPDF's span `font` and `color` as `0xRRGGBB`.

- [ ] **Step 1: Write the failing test** — append to `ingest/tests/test_pdf_text.py`:

```python


def test_spans_carry_font_and_colour(gpo_pages):
    span = line_with(gpo_pages[10], "GPO and Congress").spans[0]
    assert (span.font, round(span.size, 1), span.color) == ("HelveticaNeueLTStd-Md", 10.0, 0x646E74)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ingest && uv run pytest tests/test_pdf_text.py -q -p no:warnings -k font_and_colour`
Expected: FAIL — `AttributeError: 'Span' object has no attribute 'font'`

- [ ] **Step 3: Add the fields** — in `ingest/model.py` replace

```python
    flags: int
    bbox: BBox

    @property
    def is_superscript(self) -> bool:
```

with

```python
    flags: int
    bbox: BBox
    font: str = ""
    color: int = 0  # sRGB as 0xRRGGBB

    @property
    def is_superscript(self) -> bool:
```

- [ ] **Step 4: Fill them in the loader** — in `ingest/parse/pdf_text.py` replace

```python
                        Span(normalize(text), round(s["size"], 2), s["flags"], tuple(s["bbox"]))
```

with

```python
                        Span(normalize(text), round(s["size"], 2), s["flags"], tuple(s["bbox"]), s["font"], s["color"])
```

- [ ] **Step 5: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 111 passed, 1 deselected

- [ ] **Step 6: Commit**

```bash
git add ingest/model.py ingest/parse/pdf_text.py ingest/tests/test_pdf_text.py
git commit -m "Record font and colour on spans"
```

---

### Task 2: Currency cells and number hyphens

**Files:**
- Modify: `ingest/parse/tables.py` (add `merge_symbol_cells`)
- Modify: `ingest/parse/pdf_text.py` (`join_lines`)
- Test: `ingest/tests/test_tables.py`, `ingest/tests/test_pdf_text.py`

**Interfaces:**
- Consumes: `parse.tables`, `parse.pdf_text.join_lines` (sub-project 2).
- Produces: `parse.tables.merge_symbol_cells(rows: list[list[str]], symbols: tuple[str, ...] = ("$",)) -> list[list[str]]`; `join_lines` keeps a line-end hyphen after a digit and joins without a space when the next line starts with a digit (`"103-" + "40"` → `"103-40"`).

- [ ] **Step 1: Write the failing tests** — append to `ingest/tests/test_tables.py`:

```python


def test_lone_dollar_cells_join_the_value_after_them():
    from parse.tables import merge_symbol_cells

    rows = [["Fund balance with Treasury (Note 2)", "$", "644,807", "$", "585,883"], ["Inventory", "57,369"]]
    assert merge_symbol_cells(rows) == [["Fund balance with Treasury (Note 2)", "$644,807", "$585,883"], ["Inventory", "57,369"]]
```

and to `ingest/tests/test_pdf_text.py`:

```python


def test_join_lines_keeps_number_ranges_together():
    from parse.pdf_text import join_lines

    assert join_lines("under Public Law 103-", "40, GPO has", frozenset()) == ("under Public Law 103-40, GPO has", 21)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_tables.py tests/test_pdf_text.py -q -p no:warnings -k "dollar or number_ranges"`
Expected: 2 failed — `ImportError: cannot import name 'merge_symbol_cells'` and `AssertionError` (`'under Public Law 103- 40, GPO has'`)

- [ ] **Step 3: Add `merge_symbol_cells`** — append to `ingest/parse/tables.py`:

```python


def merge_symbol_cells(rows: list[list[str]], symbols: tuple[str, ...] = ("$",)) -> list[list[str]]:
    """Join a lone currency-symbol cell to the value after it: ["$", "644,807"] → ["$644,807"]."""
    merged = []
    for row in rows:
        out: list[str] = []
        for cell in row:
            if out and out[-1] in symbols:
                out[-1] += cell
            else:
                out.append(cell)
        merged.append(out)
    return merged
```

- [ ] **Step 4: Teach `join_lines` about number hyphens** — in `ingest/parse/pdf_text.py`, inside `join_lines`, replace

```python
    hyphen = _TRAILING_HYPHEN.search(left)
```

with

```python
    if left.endswith("-") and left[-2:-1].isdigit() and right[:1].isdigit():
        return left + right, len(left)  # "Public Law 103-" + "40" → "103-40"
    hyphen = _TRAILING_HYPHEN.search(left)
```

- [ ] **Step 5: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 113 passed, 1 deselected

- [ ] **Step 6: Commit**

```bash
git add ingest/parse/tables.py ingest/parse/pdf_text.py ingest/tests/test_tables.py ingest/tests/test_pdf_text.py
git commit -m "Merge currency cells and number hyphens"
```

---

### Task 3: Classify GPO lines

**Files:**
- Create: `ingest/chunk/narrative.py` (classification part; `build` comes in Task 4)
- Modify: `ingest/tests/helpers.py` (add `page_of`)
- Modify: `ingest/tests/conftest.py` (add `gpo_prepared`)
- Test: `ingest/tests/test_narrative_lines.py`

**Interfaces:**
- Consumes: `model.Line`, `model.Page`, `model.Span` (with `font`, `color` from Task 1); `parse.boilerplate.in_margin`; `pipeline.prepare`; `profiles.DOCS`.
- Produces (`chunk.narrative`):
  - `Style(font: str, size: float, color: int)` with `.matches(span) -> bool`
  - `NarrativeLayout` frozen dataclass: `narrative_last_page=30`, `level1`, `level2`, `caption=("HelveticaNeueLTStd-Roman", 8.2)`, `injected={2: "Letter of Transmittal", 7: "About GPO"}`, `single_page_sections={14: "GPO By the Numbers FY 2018"}`, `table_pages=frozenset({14, 58, 59, 60})`; `GPO_LAYOUT = NarrativeLayout()`
  - `Heading(level: int, title: str, rest: Line | None = None)`
  - `narrative_heading(line, layout) -> Heading | None`
  - `is_bold(line) -> bool`, `standalone(line, page) -> bool`
  - `repeated_titles(pages, layout) -> set[str]`
  - `financial_heading(line, page, repeated: set[str]) -> Heading | None`
  - `is_caption(line, layout) -> bool`
  - `merge_drop_caps(page, body_size: float) -> Page`
  - `hoist_page_title(page, is_title: Callable[[Line], bool]) -> Page`
  - test helper `page_of(pages_or_prepared_pages, number) -> Page`; session fixture `gpo_prepared` (= `prepare(DOCS["gpo"])`)

- [ ] **Step 1: Add the helper and fixture**

Append to `ingest/tests/helpers.py`:

```python


def page_of(pages, number):
    return next(page for page in pages if page.number == number)
```

Append to `ingest/tests/conftest.py`:

```python


@pytest.fixture(scope="session")
def gpo_prepared():
    from pipeline import prepare
    from profiles import DOCS

    return prepare(DOCS["gpo"])
```

- [ ] **Step 2: Write the failing tests**

`ingest/tests/test_narrative_lines.py`:
```python
from chunk.narrative import (
    GPO_LAYOUT,
    Heading,
    Style,
    financial_heading,
    hoist_page_title,
    is_caption,
    merge_drop_caps,
    narrative_heading,
    repeated_titles,
)
from helpers import line_with, make_line, make_page, page_of
from model import Span


def test_style_matches_font_size_and_colour():
    style = Style("HelveticaNeueLTStd-Md", 9.5, 0x507CBF)
    assert style.matches(Span("govinfo", 9.52, 4, (0, 0, 1, 1), "HelveticaNeueLTStd-Md", 0x507CBF))
    assert not style.matches(Span("govinfo", 9.52, 4, (0, 0, 1, 1), "HelveticaNeueLTStd-Md", 0x404041))


def test_grey_title_is_level_one(gpo_prepared):
    line = line_with(page_of(gpo_prepared.pages, 11), "GPO and Congress")
    assert narrative_heading(line, GPO_LAYOUT) == Heading(1, "GPO and Congress")


def test_run_in_heading_keeps_the_rest_of_the_line_as_body(gpo_prepared):
    heading = narrative_heading(line_with(page_of(gpo_prepared.pages, 17), "Partnership with Industry"), GPO_LAYOUT)
    assert (heading.level, heading.title) == (2, "Partnership with Industry")
    assert heading.rest.text.startswith("Other than")


def test_dark_grey_emphasis_is_not_a_heading(gpo_prepared):
    line = line_with(page_of(gpo_prepared.pages, 8), "Facebook facebook.com/USGPO")
    assert narrative_heading(line, GPO_LAYOUT) is None


def test_photo_caption_style(gpo_prepared):
    page = page_of(gpo_prepared.pages, 10)
    assert is_caption(line_with(page, "(Above) GPO employees producing the Congressional Record."), GPO_LAYOUT)
    assert not is_caption(line_with(page, "System, or FDsys. FDsys is scheduled"), GPO_LAYOUT)


def test_financial_headings_skip_table_labels_and_repeated_titles(gpo_prepared):
    repeated = repeated_titles(gpo_prepared.pages, GPO_LAYOUT)
    assert repeated == {"U.S. Government Publishing Office"}
    p35, p58, p69 = (page_of(gpo_prepared.pages, n) for n in (35, 58, 69))
    assert financial_heading(next(l for l in p35.lines if l.text == "Mission"), p35, repeated) == Heading(2, "Mission")
    assert financial_heading(next(l for l in p69.lines if l.text == "Unrestricted"), p69, repeated) is None
    title = next(l for l in p58.lines if l.text == "U.S. Government Publishing Office")
    assert financial_heading(title, p58, repeated) is None


def test_divider_page_words_are_level_one(gpo_prepared):
    p34 = page_of(gpo_prepared.pages, 34)
    assert financial_heading(next(l for l in p34.lines if l.text == "MANAGEMENT"), p34, set()) == Heading(1, "MANAGEMENT")


def test_drop_cap_rejoins_its_word(gpo_prepared):
    page = merge_drop_caps(page_of(gpo_prepared.pages, 7), gpo_prepared.body_size)
    assert any(l.text.startswith("The U.S. Government Publishing Office (GPO),") for l in page.lines)
    assert not any(l.text == "T" for l in page.lines)


def test_page_top_title_moves_before_body_listed_first():
    page = make_page(4, [make_line("Body below the title", y0=100), make_line("Title", y0=50), make_line("Lower title", y0=300)])
    moved = hoist_page_title(page, lambda l: l.text in {"Title", "Lower title"})
    assert [l.text for l in moved.lines] == ["Title", "Body below the title", "Lower title"]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_narrative_lines.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'chunk.narrative'`

- [ ] **Step 4: Write `ingest/chunk/narrative.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_narrative_lines.py -q -p no:warnings`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add ingest/chunk/narrative.py ingest/tests/helpers.py ingest/tests/conftest.py ingest/tests/test_narrative_lines.py
git commit -m "Classify GPO headings and captions"
```

---

### Task 4: Build GPO sections and chunks

**Files:**
- Modify: `ingest/chunk/narrative.py` (add `build` and constants)
- Modify: `ingest/profiles.py` (register `narrative`, GPO skip pages)
- Test: `ingest/tests/test_narrative_build.py`

**Interfaces:**
- Consumes: Task 3 functions; `chunk.common.count_tokens`, `pack`, `paragraphs_from_pages`; `parse.boilerplate.in_margin`; `parse.tables.merge_symbol_cells`, `region_rows`, `split_table`; `pipeline.BuildResult`, `DocSpec`, `Prepared`, `prepare`.
- Produces:
  - `chunk.narrative.TARGET_TOKENS = 500`, `OVERLAP_TOKENS = 75`, `TABLE_TOKENS = 450`
  - `chunk.narrative.build(spec: DocSpec, prepared: Prepared, layout: NarrativeLayout = GPO_LAYOUT) -> BuildResult` — section keys `"root"`, `"s1"`, `"s2"`, …; chunk kinds `text`, `caption`, `table`
  - `profiles.DOCS["gpo"]` → `profile="narrative"`, `skip_pages=frozenset({1, 31, 32, 33})`; `profiles.PROFILES["narrative"] = narrative.build`

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_narrative_build.py`:
```python
import pytest

from chunk.common import count_tokens
from chunk.narrative import build
from pipeline import prepare
from profiles import DOCS


@pytest.fixture(scope="module")
def built():
    spec = DOCS["gpo"]
    result = build(spec, prepare(spec))
    return result, {s.key: s for s in result.sections}


def hits(built, needle):
    result, by_key = built
    return [(c.kind, by_key[c.section_key].path) for c in result.chunks if needle in c.content]


def test_gpo_uses_the_narrative_profile_and_skips_covers():
    assert DOCS["gpo"].profile == "narrative"
    assert DOCS["gpo"].skip_pages == frozenset({1, 31, 32, 33})


def test_section_tree_has_the_expected_titles(built):
    paths = [s.path for s in built[0].sections]
    for path in [
        "Letter of Transmittal",
        "A Message from the Acting Deputy Director",
        "About GPO",
        "About GPO › History",
        "GPO and Congress",
        "GPO By the Numbers FY 2018",
        "GPO and Open, Transparent Government › Federal Depository Library Program",
        "GPO and Open, Transparent Government › govinfo",
        "SECTION I MANAGEMENT DISCUSSION AND ANALYSIS (UNAUDITED)",
        "SECTION IV FINANCIAL STATEMENTS › Consolidated Balance Sheets As of September 30, 2018 and 2017",
    ]:
        assert path in paths, path
    assert not {"U.S. Government Publishing Office", "Restricted:", "Unrestricted", "SECTION I", "MANAGEMENT"} & {
        s.title for s in built[0].sections
    }


def test_brief_facts_land_in_the_right_sections(built):
    assert ("text", "A Message from the Acting Deputy Director") in hits(built, "visited 166 depository libraries")
    assert ("text", "GPO and Open, Transparent Government › Federal Depository Library Program") in hits(
        built, "visited 166 depository libraries"
    )
    assert hits(built, "full-scale deployment of govinfo") == [("text", "A Message from the Acting Deputy Director")]
    assert hits(built, "procured more than $375 million") == [("text", "A Message from the Acting Deputy Director")]
    assert ("table", "GPO By the Numbers FY 2018") in hits(built, "$375.7 million")


def test_captions_are_separate_chunks(built):
    result, _ = built
    caption = "(Above) GPO employees producing the Congressional Record."
    assert [kind for kind, _ in hits(built, caption)] == ["caption"]
    assert sum(c.kind == "caption" for c in result.chunks) == 20


def test_statement_tables_are_aligned_and_not_repeated_as_text(built):
    result, _ = built
    tables = [c for c in result.chunks if c.kind == "table"]
    assert [c.page_start for c in tables] == [14, 58, 58, 59, 60, 60]
    assert any("| Fund balance with Treasury (Note 2) | $644,807 | $585,883 |" in c.content for c in tables)
    assert all(c.content.startswith("| (Dollars in Thousands) |") for c in tables if c.page_start >= 58)
    assert not hits(built, "Fund balance with Treasury (Note 2)") or all(k == "table" for k, _ in hits(built, "Fund balance with Treasury (Note 2)"))


def test_drop_cap_and_number_hyphen_are_repaired(built):
    assert ("text", "About GPO") in hits(built, "The U.S. Government Publishing Office (GPO), a legislative branch agency")
    assert hits(built, "Public Law 103-40")
    assert not hits(built, "103- 40")


def test_chunks_stay_inside_their_section_and_size(built):
    result, by_key = built
    for chunk in result.chunks:
        section = by_key[chunk.section_key]
        assert section.page_start <= chunk.page_start <= chunk.page_end <= section.page_end
        assert count_tokens(chunk.content) <= 600
    assert sum(c.kind == "text" for c in result.chunks) >= 100
    assert len(result.sections) >= 100
    assert by_key[next(s.key for s in result.sections if s.path.endswith("› govinfo"))].content
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_narrative_build.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'build' from 'chunk.narrative'`

- [ ] **Step 3: Add `build` to `ingest/chunk/narrative.py`**

Replace the import block at the top of the file with:

```python
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable

from chunk.common import count_tokens, pack, paragraphs_from_pages
from model import ChunkDraft, Line, Page, SectionDraft, Span
from parse.boilerplate import in_margin
from parse.tables import merge_symbol_cells, region_rows, split_table
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 500
OVERLAP_TOKENS = 75
TABLE_TOKENS = 450
```

and append to the end of the file:

```python


def build(spec: DocSpec, prepared: Prepared, layout: NarrativeLayout = GPO_LAYOUT) -> BuildResult:
    pages = [merge_drop_caps(p, prepared.body_size) for p in prepared.pages]
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

    for page in pages:
        resume = None
        if page.number in layout.single_page_sections:  # a stand-alone page (infographic) between sections
            resume = (state["l1"], state["current"])
            open_section(1, layout.single_page_sections[page.number], page.number, None)
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
        rows = merge_symbol_cells(region_rows(replace(page, lines=tuple(lines)), (0, 0, page.width, page.height)))
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
```

- [ ] **Step 4: Register the profile** — in `ingest/profiles.py` replace

```python
from chunk import plain
```

with

```python
from chunk import narrative, plain
```

replace

```python
    "gpo": DocSpec("gpo", "GPO 2018 Annual Report", DOCS_DIR / "GPO-Anual-report.pdf", "plain"),
```

with

```python
    "gpo": DocSpec(
        "gpo",
        "GPO 2018 Annual Report",
        DOCS_DIR / "GPO-Anual-report.pdf",
        "narrative",
        skip_pages=frozenset({1, 31, 32, 33}),  # covers and the statements' contents page
    ),
```

and replace

```python
PROFILES: dict[str, Profile] = {"plain": plain.build}
```

with

```python
PROFILES: dict[str, Profile] = {"plain": plain.build, "narrative": narrative.build}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_narrative_build.py -q -p no:warnings`
Expected: 7 passed

- [ ] **Step 6: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 129 passed, 1 deselected

- [ ] **Step 7: Commit**

```bash
git add ingest/chunk/narrative.py ingest/profiles.py ingest/tests/test_narrative_build.py
git commit -m "Build GPO sections and chunks"
```

---

### Task 5: GPO validation checks and live ingest

**Files:**
- Modify: `ingest/validate.py`
- Test: `ingest/tests/test_validate.py`

**Interfaces:**
- Consumes: `validate.CheckResult`, `validate.generic_checks`, `store.store_document` (test), sub-project 2 fixtures.
- Produces:
  - `validate.DOC_CHECKS: dict[str, list[Callable[[psycopg.Connection, str], CheckResult]]]` — checks now receive the slug (so tests can run them against `test-*` documents); `DOC_CHECKS["gpo"]` holds the seven checks below
  - `validate.chunk_with(needle: str, kind: str | None = None, path: str | None = None)`, `validate.kind_at_least(kind: str, minimum: int)`, `validate.section_exists(path: str)` — each returns a check `(conn, slug) -> CheckResult`

- [ ] **Step 1: Write the failing tests** — append to `ingest/tests/test_validate.py`:

```python


def store_gpo_like(conn, slug, with_table=True):
    settings = load_settings()
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    sections = [
        SectionDraft("root", "GPO", 0, "", 2, 82),
        SectionDraft("msg", "A Message from the Acting Deputy Director", 1, "A Message from the Acting Deputy Director", 4, 6, "root"),
        SectionDraft("otg", "GPO and Open, Transparent Government", 1, "GPO and Open, Transparent Government", 19, 23, "root"),
        SectionDraft("gi", "govinfo", 2, "GPO and Open, Transparent Government › govinfo", 22, 23, "otg"),
        SectionDraft("num", "GPO By the Numbers FY 2018", 1, "GPO By the Numbers FY 2018", 14, 14, "root"),
    ]
    chunks = [
        ChunkDraft("msg", "text", "in FY 2018 GPO staff visited 166 depository libraries in 25 states", 5, 5),
        ChunkDraft("msg", "text", "was the full-scale deployment of govinfo, our public-facing repository", 4, 4),
        ChunkDraft("msg", "text", "GPO procured more than $375 million worth of printing", 5, 5),
    ]
    chunks += [ChunkDraft("msg", "caption", f"Caption {i}.", 5, 5) for i in range(15)]
    if with_table:
        chunks += [ChunkDraft("num", "table", "| Revenues from procured printing | $375.7 million |", 14, 14) for _ in range(4)]
    store_document(
        conn, DocumentMeta(slug, "GPO", "narrative", 82), sections, chunks,
        [c.content for c in chunks], [vec] * len(chunks), settings.embed_model, settings.embed_dim,
    )


def test_gpo_checks_pass_on_a_document_with_the_brief_facts(conn):
    from validate import DOC_CHECKS

    store_gpo_like(conn, "test-gpo")
    results = [check(conn, "test-gpo") for check in DOC_CHECKS["gpo"]]
    assert len(results) == 7
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_gpo_checks_report_a_missing_table_fact(conn):
    from validate import DOC_CHECKS

    store_gpo_like(conn, "test-gpo", with_table=False)
    failed = [r.name for r in (check(conn, "test-gpo") for check in DOC_CHECKS["gpo"]) if not r.ok]
    assert failed == ["table chunk with '$375.7 million' in GPO By the Numbers FY 2018", "at least 4 table chunks"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_validate.py -q -p no:warnings`
Expected: 2 failed — `KeyError: 'gpo'`

- [ ] **Step 3: Add the GPO checks to `ingest/validate.py`**

Replace

```python
# Per-document checks are added by the profile sub-projects (3: gpo, 4: gep, 5: imac).
DOC_CHECKS: dict[str, list[Callable[[psycopg.Connection], CheckResult]]] = {}
```

with

```python
Check = Callable[[psycopg.Connection, str], CheckResult]


def chunk_with(needle: str, kind: str | None = None, path: str | None = None) -> Check:
    name = f"{kind or 'any'} chunk with {needle!r}" + (f" in {path}" if path else "")

    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id JOIN sections s ON s.id = c.section_id "
            "WHERE d.slug = %s AND strpos(c.content, %s) > 0 "
            "AND (%s::text IS NULL OR c.kind = %s) AND (%s::text IS NULL OR s.path = %s)",
            (slug, needle, kind, kind, path, path),
        ).fetchone()[0]
        return CheckResult(name, n > 0, f"{n} found")

    return check


def kind_at_least(kind: str, minimum: int) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = %s AND c.kind = %s",
            (slug, kind),
        ).fetchone()[0]
        return CheckResult(f"at least {minimum} {kind} chunks", n >= minimum, f"{n} found")

    return check


def section_exists(path: str) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM sections s JOIN documents d ON d.id = s.doc_id WHERE d.slug = %s AND s.path = %s",
            (slug, path),
        ).fetchone()[0]
        return CheckResult(f"section {path!r}", n > 0, f"{n} found")

    return check


MESSAGE = "A Message from the Acting Deputy Director"

# Per-document checks (spec §9); gep and imac are added by sub-projects 4 and 5.
DOC_CHECKS: dict[str, list[Check]] = {
    "gpo": [
        chunk_with("visited 166 depository libraries", "text"),
        chunk_with("full-scale deployment of govinfo", "text", MESSAGE),
        chunk_with("procured more than $375 million", "text"),
        chunk_with("$375.7 million", "table", "GPO By the Numbers FY 2018"),
        section_exists("GPO and Open, Transparent Government › govinfo"),
        kind_at_least("caption", 15),
        kind_at_least("table", 4),
    ],
}
```

and in `main`, replace

```python
            results = generic_checks(conn, settings, slug) + [check(conn) for check in DOC_CHECKS.get(slug, [])]
```

with

```python
            results = generic_checks(conn, settings, slug) + [check(conn, slug) for check in DOC_CHECKS.get(slug, [])]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_validate.py -q -p no:warnings`
Expected: 5 passed

- [ ] **Step 5: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 131 passed, 1 deselected

- [ ] **Step 6: Live ingest of GPO (real Gemini embeddings)**

Run: `cd ingest && uv run python ingest.py --doc gpo`
Expected: a report like
```
gpo — GPO 2018 Annual Report
  pages 82 · sections 114 · notes 0 · boilerplate patterns 5
  chunks: caption 20, table 6, text <≈126> (total <≈152>)
  unresolved footnote markers: none
```

Run: `cd ingest && uv run python validate.py --doc gpo`
Expected: 13 `✓ gpo:` lines (6 generic + 7 GPO), exit 0.

Run (from the repo root): `docker compose exec -T db psql -U rag -d rag -Atc "SELECT s.path FROM chunks c JOIN sections s ON s.id = c.section_id JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'gpo' AND c.content LIKE '%full-scale deployment of govinfo%'"`
Expected: `A Message from the Acting Deputy Director`

- [ ] **Step 7: Commit**

```bash
git add ingest/validate.py ingest/tests/test_validate.py
git commit -m "Add GPO validation checks"
```

---

## Sub-project acceptance (spec §10, row 3)

- [ ] `cd ingest && uv run pytest -q` → 131 passed, 1 deselected
- [ ] `uv run python ingest.py --doc gpo` → ~114 sections; 20 caption, 6 table, ~126 text chunks
- [ ] `uv run python validate.py --doc gpo` → all ✓ (6 generic + 7 GPO), exit 0
- [ ] The three brief facts are in stored chunks: 166 libraries (message + FDLP section), govinfo deployment (message), $375 million (message text) and $375.7 million (By-the-Numbers table)
- [ ] `git log --oneline main..HEAD` shows the spec update, the plan, and 5 short task commits with no trailers

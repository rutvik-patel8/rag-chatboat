# iMac Visual Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `plain` profile for the Apple iMac Product Environmental Report (12 pages) with a `visual` profile. Each page is transcribed by Gemini vision into text, table, chart and callout blocks. The numbers it reads are checked against the PDF's text layer, so facts that live in charts, small tables, icon callouts and endnotes become retrievable chunks.

**Architecture:**
- **Describer:** `enrich/vision.py` renders a page to PNG and asks a rate-limited `Generator.generate_json` for blocks under a JSON schema. It caches the raw answer by image hash and cleans it up: footnote markers become refs, text is NFKC-normalized, wrapped table rows are rejoined and repeated headings are dropped.
- **Pipeline hook:** a new hook hands the profile a `prepared.describe(page)` function when `DocSpec.vision` is set.
- **Profile:** `chunk/visual.py` makes one section per page title. It packs text blocks into text chunks, emits tables, charts and callouts as their own chunks, and marks estimated chart values "(approximate, read from chart)". On the endnote pages it keeps only tables and links them to their note. If vision fails for a page, it falls back to that page's text layer.
- **Validation:** `validate.py` gains iMac checks.

**Tech Stack:** Python 3 + uv, PyMuPDF (`get_pixmap`), `google-genai` structured output with image parts, the ingestion core, pytest, Docker Postgres.

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`. This plan implements §10 row 5 using §5.4 (rewritten from probes in commit `0490884`) and §9.

## Global Constraints

- Run every command from `ingest/` with `uv run`. Docker DB must be up.
- Vision model: `GEMINI_VISION_MODEL` defaults to the summary model (`GEMINI_SUMMARY_MODEL`, itself defaulting to `GEMINI_CHAT_MODEL`). Generation is rate-limited by `GEMINI_CHAT_REQUESTS_PER_MINUTE`.
- Page images: **150 dpi** PNG. The cache key is model + `PROMPT_VERSION` (`"v1"`) + SHA-256 of the image, and the cache lives in `ingest/.cache/imac/vision/`.
- Chart estimates carry the suffix **`(approximate, read from chart)`** and `metadata.estimated = true`.
- Sections come from the 24pt (≥ 20pt) title lines. Consecutive pages with the same title share a section.
- iMac endnote pages are **11–12**. There, only table blocks are kept, and a table links to the note naming **≥ 2** of its row labels.
- Text chunks: **400/50** tokens per section.
- Commits: one per task, short imperative subject only (≤ 50 chars). **No `Co-Authored-By` trailer, no body.**

## Review Focus

1. **The model ignores the marker rule** ("emissions7", "STAR.^18", superscript "⁷"). The listed refs must come out of the text and attach as footnotes, and digits that are part of a value (`CO2e`, `0.59W`) must stay. Pinned in Tasks 2 and 4.
2. **The model invents or misreads a number.** A chart value it calls printed that the page doesn't print becomes approximate, and table or callout numbers missing from the text layer appear in the report. Pinned in Task 4.
3. **Vision quota or failure for one page** (the free tier allows few generations a day). That page falls back to its text layer with a warning, and the rest of the document still ingests. Pinned in Task 4.
4. **Endnote pages duplicating note text.** Only the power table survives there, linked to note 18; the notes themselves stay as the pipeline's footnote chunks. Pinned in Task 4.
5. **Reruns.** Vision answers are cached by image hash, so a rerun makes no vision calls, and changing the prompt version or model re-asks. Pinned in Task 2.

---

### Task 1: Structured vision generation

**Files:**
- Modify: `ingest/config.py` (`vision_model`)
- Modify: `ingest/gemini.py` (add `Generator.generate_json`)
- Modify: `.env.example`
- Test: `ingest/tests/test_gemini.py`, `ingest/tests/test_config.py`

**Interfaces:**
- Consumes: `gemini.Generator`, `with_retry`, `RateLimiter`; `config.Settings` (sub-projects 1–4).
- Produces:
  - `config.Settings.vision_model: str = ""`, filled from `GEMINI_VISION_MODEL`, else `summary_model`
  - `gemini.Generator.generate_json(prompt: str, schema: dict, image_png: bytes | None = None) -> dict`. It runs at temperature 0 with `response_mime_type="application/json"` and `response_schema=schema`. With an image, `contents` is `[Part(png), prompt]`. An empty answer raises `RuntimeError`, and the limiter is asked for 1.

- [ ] **Step 1: Write the failing tests.** Append to `ingest/tests/test_gemini.py`:

```python


class FakeJsonModels:
    def __init__(self, text='{"blocks": []}'):
        self.text = text
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        return SimpleNamespace(text=self.text)


def test_generate_json_sends_the_image_and_schema_and_parses_the_answer():
    from gemini import Generator

    models = FakeJsonModels('{"blocks": [{"kind": "text", "markdown": "Use"}]}')
    generator = Generator(SimpleNamespace(models=models), "gemini-3.5-flash-lite", sleep=lambda s: None)
    schema = {"type": "object"}
    assert generator.generate_json("Transcribe", schema, b"\x89PNG") == {"blocks": [{"kind": "text", "markdown": "Use"}]}
    call = models.calls[0]
    assert call.contents[1] == "Transcribe" and call.contents[0].inline_data.data == b"\x89PNG"
    assert (call.config.temperature, call.config.response_mime_type, call.config.response_schema) == (0, "application/json", schema)


def test_generate_json_rejects_empty_answers_and_asks_the_limiter():
    from gemini import Generator

    asked = []
    generator = Generator(SimpleNamespace(models=FakeJsonModels("")), "m", sleep=lambda s: None, limiter=SimpleNamespace(acquire=asked.append))
    with pytest.raises(RuntimeError, match="no text"):
        generator.generate_json("x", {})
    assert asked == [1]
```

Append to `ingest/tests/test_config.py`:

```python


def test_vision_model_defaults_to_the_summary_model():
    assert load_settings(VALID).vision_model == "gemini-3.8-flash"
    lite = {**VALID, "GEMINI_SUMMARY_MODEL": "gemini-3.5-flash-lite"}
    assert load_settings(lite).vision_model == "gemini-3.5-flash-lite"
    assert load_settings({**lite, "GEMINI_VISION_MODEL": "gemini-3.8-flash"}).vision_model == "gemini-3.8-flash"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_gemini.py tests/test_config.py -q -p no:warnings -k "generate_json or vision_model"`
Expected: 3 failed. Two with `AttributeError: 'Generator' object has no attribute 'generate_json'`, and one with `AttributeError: 'Settings' object has no attribute 'vision_model'`.

- [ ] **Step 3: Add the setting.** In `ingest/config.py`, replace

```python
    summary_model: str = ""  # model for ingest-time summaries; empty means chat_model
```

with

```python
    summary_model: str = ""  # model for ingest-time summaries; empty means chat_model
    vision_model: str = ""  # model for page transcription (visual profile); empty means summary_model
```

replace

```python
    embed_tokens_per_minute = _positive_int(env, "EMBED_TOKENS_PER_MINUTE", 20000)
```

with

```python
    embed_tokens_per_minute = _positive_int(env, "EMBED_TOKENS_PER_MINUTE", 20000)
    summary_model = env.get("GEMINI_SUMMARY_MODEL", "").strip() or env["GEMINI_CHAT_MODEL"].strip()
```

and replace

```python
        summary_model=env.get("GEMINI_SUMMARY_MODEL", "").strip() or env["GEMINI_CHAT_MODEL"].strip(),
    )
```

with

```python
        summary_model=summary_model,
        vision_model=env.get("GEMINI_VISION_MODEL", "").strip() or summary_model,
    )
```

- [ ] **Step 4: Add `generate_json`.** In `ingest/gemini.py`, replace

```python
import math
import time
```

with

```python
import json
import math
import time
```

and replace

```python
def make_generator(
```

with

```python
    def generate_json(self, prompt: str, schema: dict, image_png: bytes | None = None) -> dict:
        """Structured output: the answer must parse as JSON matching `schema` (optionally about one image)."""
        if self._limiter is not None:
            self._limiter.acquire(1)
        contents = [types.Part.from_bytes(data=image_png, mime_type="image/png"), prompt] if image_png else prompt
        config = types.GenerateContentConfig(temperature=0, response_mime_type="application/json", response_schema=schema)
        response = with_retry(
            lambda: self._client.models.generate_content(model=self._model, contents=contents, config=config),
            sleep=self._sleep,
        )
        if not (response.text or "").strip():
            raise RuntimeError(f"{self._model} returned no text")
        return json.loads(response.text)


def make_generator(
```

- [ ] **Step 5: Document the setting.** In `.env.example` replace

```dotenv
# GEMINI_SUMMARY_MODEL=gemini-3.5-flash-lite
```

with

```dotenv
# GEMINI_SUMMARY_MODEL=gemini-3.5-flash-lite
# Optional: model for transcribing iMac pages with vision (defaults to GEMINI_SUMMARY_MODEL)
# GEMINI_VISION_MODEL=gemini-3.5-flash-lite
```

- [ ] **Step 6: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 198 passed, 2 deselected

- [ ] **Step 7: Commit**

```bash
git add ingest/config.py ingest/gemini.py .env.example ingest/tests/test_gemini.py ingest/tests/test_config.py
git commit -m "Add structured vision generation"
```

---

### Task 2: Page describer

**Files:**
- Create: `ingest/enrich/vision.py`
- Uses: `ingest/tests/fixtures/imac_vision.json` (real `gemini-3.5-flash-lite` answers for all 12 iMac pages, committed with this plan)
- Test: `ingest/tests/test_vision.py`

**Interfaces:**
- Consumes: `cache.JsonCache`, `parse.pdf_text.normalize`, `profiles.DOCS`; a generator with `generate_json(prompt, schema, image_png)` (Task 1).
- Produces (`enrich.vision`):
  - `PROMPT_VERSION = "v1"`, `DPI = 150`, `PROMPT`, `SCHEMA`
  - `render_page(pdf_path, page_number, dpi=DPI) -> bytes` (PNG)
  - `strip_markers(text: str, refs: set[int]) -> str`
  - `clean_block(block: dict) -> dict` with keys `kind`, `title`, `markdown`, `values` (each `label`, `value`, `unit`, `estimated`) and `footnote_refs` (sorted ints)
  - `PageDescriber(generator, cache, model)`, callable as `describer(pdf_path, page_number) -> list[dict]` (cleaned blocks)

- [ ] **Step 1: Check the fixture.** It holds the raw `blocks` that `gemini-3.5-flash-lite` returned for pages 1–12, keyed by page number. That lets tests exercise real model output offline.

Run: `cd ingest && python3 -c "import json; d = json.load(open('tests/fixtures/imac_vision.json')); print(len(d), sum(map(len, d.values())))"`
Expected: `12 93`

- [ ] **Step 2: Write the failing tests.** Create `ingest/tests/test_vision.py`:

```python
from enrich.vision import PageDescriber, clean_block, render_page, strip_markers
from cache import JsonCache
from profiles import DOCS


def block(kind="text", markdown="", title="", values=(), refs=()):
    return {"kind": kind, "title": title, "markdown": markdown, "values": list(values), "footnote_refs": list(refs)}


def test_superscript_markers_become_refs():
    cleaned = clean_block(block(markdown="GHG reductions achieved⁷ ↓>20%"))
    assert cleaned["markdown"] == "GHG reductions achieved ↓>20%"
    assert cleaned["footnote_refs"] == [7]


def test_listed_refs_glued_to_words_are_removed_but_values_stay():
    cleaned = clean_block(block(markdown="ENERGY STAR.^18 Baseline emissions7: 445 kg, 0.59W, CO₂e", refs=[7, 18]))
    assert cleaned["markdown"] == "ENERGY STAR. Baseline emissions: 445 kg, 0.59W, CO2e"
    assert strip_markers("packaging3 is 100%", {3}) == "packaging is 100%"


def test_tables_rejoin_wrapped_rows_and_headings_are_not_repeated():
    table = clean_block(block("table", "| Greenhouse gas emissions | iMac (two ports)\n256GB |\n| --- | --- |"))
    assert table["markdown"] == "| Greenhouse gas emissions | iMac (two ports) 256GB |\n| --- | --- |"
    text = clean_block(block(title="Use", markdown="# Use\n\n**Designed** to last."))
    assert (text["title"], text["markdown"]) == ("", "# Use\n\nDesigned to last.")


def test_render_page_returns_a_png():
    assert render_page(DOCS["imac"].path, 2)[:4] == b"\x89PNG"


class FakeVision:
    def __init__(self):
        self.calls = 0

    def generate_json(self, prompt, schema, image_png):
        self.calls += 1
        assert image_png[:4] == b"\x89PNG" and "footnote" in prompt and schema["required"] == ["blocks"]
        return {"blocks": [block("callout", "100% of the packaging³ is fiber-based")]}


def test_describer_caches_by_page_image(tmp_path):
    vision = FakeVision()
    describer = PageDescriber(vision, JsonCache(tmp_path, "imac/vision"), "gemini-3.5-flash-lite")
    first = describer(DOCS["imac"].path, 6)
    again = describer(DOCS["imac"].path, 6)
    assert vision.calls == 1
    assert first == again == [
        {"kind": "callout", "title": "", "markdown": "100% of the packaging is fiber-based", "values": [], "footnote_refs": [3]}
    ]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_vision.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'enrich.vision'`

- [ ] **Step 4: Write `ingest/enrich/vision.py`**

```python
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
        text = re.sub(rf"(?<=[A-Za-z.)\]])\^?{n}\b", "", text)
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_vision.py -q -p no:warnings`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add ingest/enrich/vision.py ingest/tests/test_vision.py
git commit -m "Add vision page describer"
```

---

### Task 3: Vision hook in the pipeline

**Files:**
- Modify: `ingest/pipeline.py` (`DocSpec.vision`, `Prepared.describe`, `Describer`, `run(..., describer=None)`)
- Modify: `ingest/ingest.py` (wire a `PageDescriber`)
- Test: `ingest/tests/test_pipeline_db.py`

**Interfaces:**
- Consumes: `enrich.vision.PageDescriber` (Task 2), `gemini.make_generator` (Task 1).
- Produces:
  - `pipeline.DocSpec.vision: bool = False`
  - `pipeline.Prepared.describe: Callable[[int], list[dict]] | None = None`
  - `pipeline.Describer = Callable[[DocSpec, int], list[dict]]`
  - `pipeline.run(..., summarizer=None, describer=None)`. When `spec.vision` and a describer are given, it sets `prepared.describe` before calling the profile.
  - `ingest.py` caches vision answers in `ingest/.cache/<slug>/vision/`. It shares the summary generator when the two models are the same.

- [ ] **Step 1: Write the failing test.** Append to `ingest/tests/test_pipeline_db.py`:

```python


def test_vision_describer_reaches_the_profile_only_when_the_spec_asks(conn, tmp_path):
    settings = load_settings()
    seen = []

    def describer(spec, page_number):
        seen.append((spec.slug, page_number))
        return []

    def profile(spec, prepared):
        if prepared.describe is not None:
            prepared.describe(7)
        return plain.build(spec, prepared)

    spec = replace(DOCS["imac"], slug="test-imac", vision=False)
    run(spec, profile, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path, describer=describer)
    assert seen == []
    run(replace(spec, vision=True), profile, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path, describer=describer)
    assert seen == [("test-imac", 7)]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ingest && uv run pytest tests/test_pipeline_db.py -q -p no:warnings -k vision`
Expected: FAIL with `TypeError: DocSpec.__replace__() got an unexpected keyword argument 'vision'`, or a similar `TypeError` about `vision`.

- [ ] **Step 3: Add the hook.** In `ingest/pipeline.py`, replace

```python
    summaries: bool = False  # add a Gemini summary chunk per major section (spec §5.3)
```

with

```python
    summaries: bool = False  # add a Gemini summary chunk per major section (spec §5.3)
    vision: bool = False  # let the profile transcribe pages with Gemini vision (spec §5.4)
```

replace

```python
    vocab: frozenset[str] = frozenset()  # document words, for line-end hyphen decisions
```

with

```python
    vocab: frozenset[str] = frozenset()  # document words, for line-end hyphen decisions
    describe: Callable[[int], list[dict]] | None = None  # page number → vision blocks (set by run when spec.vision)
```

replace

```python
Summarizer = Callable[[DocSpec, list[SectionDraft], list[str]], list[ChunkDraft]]  # appends warnings
```

with

```python
Summarizer = Callable[[DocSpec, list[SectionDraft], list[str]], list[ChunkDraft]]  # appends warnings
Describer = Callable[[DocSpec, int], list[dict]]  # page number → vision blocks
```

and replace

```python
    spec: DocSpec, profile: Profile, settings: Settings, conn, embedder, cache_root: Path, summarizer: Summarizer | None = None
) -> Report:
    report = Report(spec.slug, spec.title)
    prepared = prepare(spec)
```

with

```python
    spec: DocSpec,
    profile: Profile,
    settings: Settings,
    conn,
    embedder,
    cache_root: Path,
    summarizer: Summarizer | None = None,
    describer: Describer | None = None,
) -> Report:
    report = Report(spec.slug, spec.title)
    prepared = prepare(spec)
    if spec.vision and describer is not None:
        prepared.describe = lambda page_number: describer(spec, page_number)
```

- [ ] **Step 4: Wire the CLI.** In `ingest/ingest.py`, replace

```python
from enrich.summaries import summary_chunks
```

with

```python
from enrich.summaries import summary_chunks
from enrich.vision import PageDescriber
```

replace

```python
    def summarizer(spec, sections, warnings):
```

with

```python
    vision = generator if settings.vision_model == settings.summary_model else make_generator(settings, settings.vision_model)

    def describer(spec, page_number):
        cache = JsonCache(CACHE_ROOT, f"{spec.slug}/vision")
        return PageDescriber(vision, cache, settings.vision_model)(spec.path, page_number)

    def summarizer(spec, sections, warnings):
```

and replace

```python
            report = run(spec, PROFILES[spec.profile], settings, conn, embedder, CACHE_ROOT, summarizer)
```

with

```python
            report = run(spec, PROFILES[spec.profile], settings, conn, embedder, CACHE_ROOT, summarizer, describer)
```

- [ ] **Step 5: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 204 passed, 2 deselected

- [ ] **Step 6: Commit**

```bash
git add ingest/pipeline.py ingest/ingest.py ingest/tests/test_pipeline_db.py
git commit -m "Add vision hook to the pipeline"
```

---

### Task 4: Visual profile for the iMac report

**Files:**
- Create: `ingest/chunk/visual.py`
- Modify: `ingest/profiles.py` (register `visual`; iMac uses it with `vision=True`)
- Test: `ingest/tests/test_visual.py`

**Interfaces:**
- Consumes: `chunk.common.Paragraph`, `pack`, `paragraphs_from_pages`; `model.Ref`; `pipeline.BuildResult`, `DocSpec`, `Prepared`, `prepare`; `enrich.vision.clean_block` (tests); the fixture from Task 2.
- Produces (`chunk.visual`):
  - `TARGET_TOKENS = 400`, `OVERLAP_TOKENS = 50`, `TITLE_SIZE = 20.0`, `APPROXIMATE = "(approximate, read from chart)"`
  - `page_title(page) -> str | None`, `numbers(text) -> set[str]`, `verify(block, page_numbers, page_no) -> tuple[dict, list[str]]`, `block_content(block) -> str`, `table_notes(block, notes) -> list[int]`
  - `build(spec, prepared) -> BuildResult`, which emits chunk kinds `text`, `table`, `chart` and `callout`, and fills `BuildResult.warnings`
  - `profiles.DOCS["imac"]` with `profile="visual"` and `vision=True`; `profiles.PROFILES["visual"] = visual.build`

- [ ] **Step 1: Write the failing tests.** Create `ingest/tests/test_visual.py`:

```python
import json
from pathlib import Path

import pytest

from chunk.visual import APPROXIMATE, block_content, build, page_title, table_notes, verify
from enrich.vision import clean_block
from helpers import page_of
from model import Note
from pipeline import prepare
from profiles import DOCS, PROFILES

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "imac_vision.json").read_text())


def described(fail_on=()):
    def describe(page_number):
        if page_number in fail_on:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return [clean_block(b) for b in FIXTURE[str(page_number)]]

    return describe


@pytest.fixture(scope="module")
def imac():
    prepared = prepare(DOCS["imac"])
    prepared.describe = described()
    result = build(DOCS["imac"], prepared)
    return result, {s.key: s for s in result.sections}, prepared


def test_imac_uses_the_visual_profile_with_vision():
    assert (DOCS["imac"].profile, DOCS["imac"].vision) == ("visual", True)
    assert "visual" in PROFILES


def test_page_titles_join_the_large_lines(imac):
    pages = imac[2].pages
    assert page_title(page_of(pages, 3)) == "Taking responsibility for our products at every stage"
    assert page_title(page_of(pages, 10)) == "Carbon Footprint"


def test_unprinted_chart_values_become_approximate_and_unseen_table_numbers_are_reported():
    chart = {"kind": "chart", "title": "", "markdown": "Baseline emissions: 445 kg", "footnote_refs": [],
             "values": [{"label": "Use", "value": "130", "unit": "kg", "estimated": False}]}
    fixed, warnings = verify(chart, {"445"}, 2)
    assert fixed["values"][0]["estimated"] is True
    assert warnings == ["p2: chart value Use=130 not printed; marked approximate"]
    table = {"kind": "table", "title": "", "markdown": "| Sleep | 0.59W | 0.95W |", "values": [], "footnote_refs": []}
    assert verify(table, {"0.59"}, 12)[1] == ["p12: table numbers not in the text layer: 0.95"]


def test_chart_content_lists_values_and_marks_estimates():
    chart = {"kind": "chart", "title": "iMac emissions", "markdown": "iMac emissions: 346 kg", "footnote_refs": [],
             "values": [{"label": "Transportation", "value": "5", "unit": "kg", "estimated": True},
                        {"label": "Total", "value": "346", "unit": "kg", "estimated": False}]}
    assert block_content(chart) == f"iMac emissions\n\niMac emissions: 346 kg\n\nValues: Transportation: 5 kg {APPROXIMATE}; Total: 346 kg"


def test_endnote_table_links_to_the_note_naming_its_rows():
    table = {"kind": "table", "title": "", "values": [], "footnote_refs": [],
             "markdown": "| Mode | 100V |\n|---|---|\n| Off | 0.27W |\n| Sleep | 0.56W |"}
    notes = [Note(17, 12, "Responsible sourcing of wood fiber."), Note(18, 12, "Off: Lowest power mode. Sleep: Low power state.")]
    assert table_notes(table, notes) == [18]


def test_one_section_per_titled_page(imac):
    result = imac[0]
    assert [s.title for s in result.sections if s.level == 1] == [
        "Product Environmental Report", "Progress toward carbon neutral",
        "Taking responsibility for our products at every stage", "Design and Source", "Make",
        "Package and Ship", "Use", "Recover", "Definitions", "Carbon Footprint", "Endnotes",
    ]
    endnotes = next(s for s in result.sections if s.title == "Endnotes")
    assert (endnotes.page_start, endnotes.page_end) == (11, 12)


def test_charts_tables_and_callouts_are_their_own_chunks(imac):
    chunks = imac[0].chunks
    kinds = [c.kind for c in chunks]
    assert (kinds.count("chart"), kinds.count("table"), kinds.count("callout")) == (3, 3, 4)
    carbon = next(c for c in chunks if c.kind == "table" and "Total product footprint" in c.content)
    assert "| Total product footprint | 346 kg CO2e |" in carbon.content and "45%" in carbon.content
    chart = next(c for c in chunks if c.kind == "chart" and "Baseline emissions: 445 kg" in c.content)
    assert APPROXIMATE in chart.content and chart.metadata == {"estimated": True}


def test_footnote_markers_become_refs_not_text(imac):
    chunks = imac[0].chunks
    use = next(c for c in chunks if c.kind == "text" and "58 percent less energy" in c.content)
    assert "requirement for ENERGY STAR." in use.content and "STAR.^18" not in use.content
    assert (7, 18) in use.refs
    assert not [c for c in chunks if any(glued in c.content for glued in ("emissions7", "packaging3", "sourced17", "Apple.7"))]


def test_endnote_pages_keep_only_the_power_table(imac):
    result, by_key, _ = imac
    endnote_chunks = [c for c in result.chunks if by_key[c.section_key].title == "Endnotes"]
    assert [c.kind for c in endnote_chunks] == ["table"]
    assert endnote_chunks[0].refs == ((12, 18),)
    assert "| Sleep | 0.56W | 0.57W | 0.59W |" in endnote_chunks[0].content


def test_vision_failure_falls_back_to_the_text_layer():
    prepared = prepare(DOCS["imac"])
    prepared.describe = described(fail_on={5})
    result = build(DOCS["imac"], prepared)
    assert [w for w in result.warnings if w.startswith("p5: vision failed")]
    assert [c for c in result.chunks if c.kind == "text" and c.page_start == 5 and "Apple Supplier Code of Conduct" in c.content]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_visual.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'chunk.visual'`

- [ ] **Step 3: Write `ingest/chunk/visual.py`**

```python
"""Visual profile for the iMac Product Environmental Report (spec §5.4).

Most facts sit in charts, tables, icon call-outs and endnotes, so each page is transcribed by a vision model
into typed blocks. Numbers the model reads are checked against the PDF's own text layer.
"""
from __future__ import annotations

import re
from collections import defaultdict

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
                warnings.append(f"p{page.number}: vision failed ({str(exc).splitlines()[0][:100]}); used the text layer")
        if blocks is None:
            paragraphs[current.key] += paragraphs_from_pages([page], prepared.vocab or None)
            continue
        page_numbers = numbers(" ".join(l.text for l in raw.get(page.number, page).lines))
        endnotes = page.number in spec.endnote_pages
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
    for section in sections:
        section.content = "\n\n".join(p.text for p in paragraphs[section.key]) or None
        for packed in pack(paragraphs[section.key], TARGET_TOKENS, OVERLAP_TOKENS):
            chunks.append(ChunkDraft(section.key, "text", packed.text, packed.page_start, packed.page_end, refs=packed.refs))
    return BuildResult(sections, chunks, warnings)
```

- [ ] **Step 4: Register the profile.** In `ingest/profiles.py`, replace

```python
from chunk import hierarchical, narrative, plain
```

with

```python
from chunk import hierarchical, narrative, plain, visual
```

replace

```python
        DOCS_DIR / "iMac_PER_Oct2024.pdf",
        "plain",
        endnote_pages=frozenset({11, 12}),
```

with

```python
        DOCS_DIR / "iMac_PER_Oct2024.pdf",
        "visual",
        endnote_pages=frozenset({11, 12}),
        vision=True,
```

and replace

```python
PROFILES: dict[str, Profile] = {"plain": plain.build, "narrative": narrative.build, "hierarchical": hierarchical.build}
```

with

```python
PROFILES: dict[str, Profile] = {
    "plain": plain.build,
    "narrative": narrative.build,
    "hierarchical": hierarchical.build,
    "visual": visual.build,
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_visual.py -q -p no:warnings`
Expected: 10 passed

- [ ] **Step 6: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 214 passed, 2 deselected

- [ ] **Step 7: Commit**

```bash
git add ingest/chunk/visual.py ingest/profiles.py ingest/tests/test_visual.py
git commit -m "Add iMac visual profile"
```

---

### Task 5: iMac validation checks and live ingest

**Files:**
- Modify: `ingest/validate.py`
- Test: `ingest/tests/test_validate.py`

**Interfaces:**
- Consumes: `validate.Check`, `chunk_with`, `kind_at_least` (sub-projects 3–4); `store.store_document` (test).
- Produces: `validate.chunk_with_footnote(needle: str, n: int) -> Check` and `validate.DOC_CHECKS["imac"]` (the eight checks below).

- [ ] **Step 1: Write the failing tests.** Append to `ingest/tests/test_validate.py`:

```python


def store_imac_like(conn, slug, energy_footnote=True):
    settings = load_settings()
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    sections = [
        SectionDraft("root", "iMac", 0, "", 1, 12),
        SectionDraft("use", "Use", 1, "Use", 7, 7, "root"),
        SectionDraft("cf", "Carbon Footprint", 1, "Carbon Footprint", 10, 10, "root"),
        SectionDraft("en", "Endnotes", 1, "Endnotes", 11, 12, "root"),
    ]
    note18 = [{"n": 18, "text": "Energy consumption values are based on ENERGY STAR."}] if energy_footnote else []
    chunks = [
        ChunkDraft("use", "text", "iMac uses 58 percent less energy than the requirement for ENERGY STAR.", 7, 7, footnotes=note18),
        ChunkDraft("use", "text", "iMac uses 100% recycled aluminum in the stand.", 1, 1),
        ChunkDraft("cf", "table", "| Product use | 45% |", 10, 10),
        ChunkDraft("en", "table", "| Sleep | 0.56W | 0.57W | 0.59W |", 12, 12),
    ]
    chunks += [ChunkDraft("cf", "chart", f"Values: Use: 130 kg (approximate, read from chart) {i}", 2, 2) for i in range(3)]
    chunks += [ChunkDraft("cf", "callout", f"76% recycled content {i}", 6, 6) for i in range(3)]
    chunks += [ChunkDraft("en", "footnote", f"Footnote {i}: text", 11, 11) for i in range(1, 20)]
    store_document(
        conn, DocumentMeta(slug, "iMac", "visual", 12), sections, chunks,
        [c.content for c in chunks], [vec] * len(chunks), settings.embed_model, settings.embed_dim,
    )


def test_imac_checks_pass_on_a_document_with_the_brief_facts(conn):
    from validate import DOC_CHECKS

    store_imac_like(conn, "test-imac")
    results = [check(conn, "test-imac") for check in DOC_CHECKS["imac"]]
    assert len(results) == 8
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_imac_checks_report_a_missing_footnote_link(conn):
    from validate import DOC_CHECKS

    store_imac_like(conn, "test-imac", energy_footnote=False)
    failed = [r.name for r in (check(conn, "test-imac") for check in DOC_CHECKS["imac"]) if not r.ok]
    assert failed == ["chunk with '58 percent less energy than the requirement for ENERGY STAR' citing footnote 18"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_validate.py -q -p no:warnings -k imac_checks`
Expected: 2 failed with `KeyError: 'imac'`

- [ ] **Step 3: Add the checks.** In `ingest/validate.py`, replace

```python
def kind_at_least(kind: str, minimum: int) -> Check:
```

with

```python
def chunk_with_footnote(needle: str, n: int) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        count = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE d.slug = %s AND strpos(c.content, %s) > 0 AND c.footnotes @> %s::jsonb",
            (slug, needle, f'[{{"n": {n}}}]'),
        ).fetchone()[0]
        return CheckResult(f"chunk with {needle!r} citing footnote {n}", count > 0, f"{count} found")

    return check


def kind_at_least(kind: str, minimum: int) -> Check:
```

and replace

```python
# Per-document checks (spec §9); imac is added by sub-project 5.
DOC_CHECKS: dict[str, list[Check]] = {
```

with

```python
# Per-document checks (spec §9).
DOC_CHECKS: dict[str, list[Check]] = {
    "imac": [
        chunk_with("0.59W", "table"),
        chunk_with("45%", "table"),
        chunk_with("approximate, read from chart", "chart"),
        chunk_with_footnote("58 percent less energy than the requirement for ENERGY STAR", 18),
        chunk_with("recycled aluminum in the stand"),
        kind_at_least("footnote", 19),
        kind_at_least("callout", 3),
        kind_at_least("chart", 3),
    ],
```

- [ ] **Step 4: Run the tests**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 216 passed, 2 deselected

- [ ] **Step 5: Live ingest of the iMac report.** It makes 12 vision calls and about 40 embeddings. If the free tier's daily quotas are spent (a 429 with a multi-hour `retryDelay`), wait for the reset rather than retrying.

Run: `cd ingest && uv run python ingest.py --doc imac`
Expected: a report like
```
imac — iMac Product Environmental Report (Oct 2024)
  pages 12 · sections 12 · notes 19 · boilerplate patterns 2
  chunks: callout 4, chart 3, footnote 19, table 3, text <≈13> (total <≈42>)
  unresolved footnote markers: none
```
The vision model's wording varies slightly between runs, so counts within one or two are fine.

Run: `cd ingest && uv run python validate.py --doc imac`
Expected: 14 `✓ imac:` lines (6 generic and 8 iMac), exit 0.

Run it again: `cd ingest && uv run python ingest.py --doc imac`
Expected: the same report in a few seconds, with no vision or embedding calls.

- [ ] **Step 6: Commit**

```bash
git add ingest/validate.py ingest/tests/test_validate.py
git commit -m "Add iMac validation checks"
```

---

## Sub-project acceptance (spec §10, row 5)

- [ ] `cd ingest && uv run pytest -q` → 216 passed, 2 deselected
- [ ] `uv run python ingest.py --doc imac` → 12 sections; 3 chart, 3 table, 4 callout and 19 footnote chunks; no unresolved markers
- [ ] `uv run python validate.py --doc imac` → all ✓. The brief's three iMac answers are stored: "58 percent less energy" with footnote 18; the p10 table (346 kg, Production 50%, Product use 45%) plus an approximate chart; "recycled aluminum in the stand"
- [ ] `git log --oneline main..HEAD` shows the spec update, the plan and 5 short task commits, with no trailers

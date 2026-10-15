# GEP Hierarchical Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `plain` profile for the World Bank's Global Economic Prospects (June 2026, 200 pages) with a `hierarchical` profile. It builds a chapter → section → sub-section tree (with boxes, regions and annexes), turns figures and tables into their own chunks, and adds a Gemini summary chunk per major section, so region- and chapter-scoped questions retrieve the right part of the report.

**Architecture:** Chapters come from PDF bookmarks; everything below comes from typography (bookmarks are wrong below chapter level). `chunk/hierarchical.py` classifies each line by role (front title, section, sub-section, minor heading, FIGURE/TABLE/BOX title, graphic text, box text, body). An `_Outline` object grows the section tree in reading order and files body lines by font size: box text is 10.6pt, chapter text 11pt. Figure and table titles collect the Helvetica lines beneath them into chart and table chunks. A new pipeline hook calls `enrich/summaries.py` to summarize level-1/2 sections with a rate-limited Gemini `Generator`. `validate.py` gains GEP checks.

**Tech Stack:** Python 3 + uv, PyMuPDF (`get_toc`), `google-genai` (`generate_content`), the ingestion core, pytest, Docker Postgres.

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`. This plan implements §10 row 4 using §5.3 (rewritten from probes in commit `98940ad`) and §9.

## Global Constraints

- Run every command from `ingest/` with `uv run`. Docker DB must be up.
- GEP skipped pages: **1–14 and 192–200**. Chapter bookmarks: `Ch. 1 Global Outlook` (p23), `Ch. 2 Regional Outlooks` (p69), `Ch. 3 …` (p107), `Ch. 4 …` (p143), `Statistical Annex` (p185).
- Heading styles: front titles `HelveticaLTStd-Cond` **26pt**; sections `HelveticaLTStd-Bold` **16 or 14pt**, any colour; sub-sections `HelveticaLTStd-Roman` **11pt** colour **`0x4475A1`**; minor `AGaramondPro-BoldItalic` **11pt** `0x4475A1`; FIGURE/TABLE/BOX titles `HelveticaLTStd-Bold` **10pt**. Box body text **10.6pt**.
- Region titles (exact): `East Asia and Pacific`, `Europe and Central Asia`, `Latin America and the Caribbean`, `Middle East, North Africa, Afghanistan, and Pakistan`, `South Asia`, `Sub-Saharan Africa`. Expected pages: 81–83, 84–87, 88–91, 92–95, **96–98**, 99–102.
- Chunk sizes: text **350/50** per leaf section; tables **≤ 400** tokens per piece, title on every piece; figure chunks unsplit.
- "References" sections stay in the tree but get **no chunks**.
- Summaries: level 1–2 sections with **≥ 300** tokens of subtree text; prompt truncated to **6,000** tokens; cache key = model + `PROMPT_VERSION` + prompt; chunk content `"Summary of <path>: <summary>"`; generation limited by `GEMINI_CHAT_REQUESTS_PER_MINUTE` (default **10**).
- Commits: one per task, short imperative subject only (≤ 50 chars). **No `Co-Authored-By` trailer, no body.**

## Review Focus

1. **Pages shared by a box and the chapter** (p35: box ends, "Global context" begins) → each line lands in the right section by font size; box sub-headings go to the box only when box text follows them. Pinned in Task 5.
2. **Wrapped headings at every level** ("Latin America and" / "the Caribbean"; "Non-linear relationship between debt and" / "interest rates") → one section, never a fragment section. Pinned in Task 5.
3. **Tables whose superscript note markers look like footnotes** → table chunks carry no footnote refs, and all 32 real page footnotes still attach to body chunks. Pinned in Task 5.
4. **Summary generation failures or quota limits** → rate-limited and retried (≥ 15s on 429), cached per prompt so a rerun never pays twice, and an empty model answer is an error rather than an empty chunk. Pinned in Tasks 2–3.
5. **Wide, digit-dense tables** (Statistical Annex, SSA forecasts) → split under the token budget with the header repeated; year cells like `2026f`/`24Q4` keep the header aligned. Pinned in Tasks 1 and 4.

---

### Task 1: Year cells count as numeric

**Files:**
- Modify: `ingest/parse/tables.py` (`_NUMERIC_CELL`)
- Test: `ingest/tests/test_tables.py`

**Interfaces:**
- Consumes: `parse.tables.pad_numeric_rows` (sub-project 3).
- Produces: `pad_numeric_rows` treats `2025e`, `2026f`, `24Q4` as numeric cells.

- [ ] **Step 1: Write the failing test.** Append to `ingest/tests/test_tables.py`:

```python


def test_year_cells_with_suffixes_count_as_numeric():
    from parse.tables import pad_numeric_rows

    rows = [["TABLE 2.9"], ["2023", "2025e", "2026f", "24Q4"], ["World", "2.8", "2.9", "2.5", "3.1"]]
    assert pad_numeric_rows(rows)[1] == ["", "2023", "2025e", "2026f", "24Q4"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ingest && uv run pytest tests/test_tables.py -q -p no:warnings -k suffixes`
Expected: FAIL. The row stays `['2023', '2025e', '2026f', '24Q4']`.

- [ ] **Step 3: Widen the pattern.** In `ingest/parse/tables.py`, replace

```python
_NUMERIC_CELL = re.compile(r"[\d$,.()%-]+")
```

with

```python
_NUMERIC_CELL = re.compile(r"[\d$,.()%-]+(?:[A-Za-z]\d*)?")  # 2018, $644,807, 2026f, 24Q4
```

- [ ] **Step 4: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 145 passed, 1 deselected

- [ ] **Step 5: Commit**

```bash
git add ingest/parse/tables.py ingest/tests/test_tables.py
git commit -m "Treat year cells as numeric"
```

---

### Task 2: Rate-limited text generation

**Files:**
- Modify: `ingest/config.py` (`chat_per_minute`, `_positive_int`)
- Modify: `ingest/gemini.py` (add `Generator`, `make_generator`)
- Modify: `.env.example`
- Test: `ingest/tests/test_gemini.py`, `ingest/tests/test_config.py`, `ingest/tests/test_gemini_live.py`

**Interfaces:**
- Consumes: `gemini.with_retry`, `gemini.RateLimiter`, `gemini.make_client`, `config.Settings` (sub-projects 1–3).
- Produces:
  - `config.Settings.chat_per_minute: int = 10` (env `GEMINI_CHAT_REQUESTS_PER_MINUTE`, positive int)
  - `config._positive_int(env, name, default) -> int` (also used for `EMBED_REQUESTS_PER_MINUTE`)
  - `gemini.Generator(client, model, sleep=time.sleep, limiter=None)` with `.generate(prompt: str) -> str` (temperature 0.2, stripped text, `RuntimeError` on an empty answer, limiter asked for 1 per call)
  - `gemini.make_generator(settings) -> Generator`

- [ ] **Step 1: Write the failing tests.** Append to `ingest/tests/test_gemini.py`:

```python


class FakeGenModels:
    def __init__(self, texts=("A summary.",), failures=()):
        self.texts = list(texts)
        self.failures = list(failures)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(text=self.texts.pop(0))


def test_generator_returns_stripped_text_at_low_temperature():
    from gemini import Generator

    models = FakeGenModels(texts=["  Growth slows.  "])
    generator = Generator(SimpleNamespace(models=models), "gemini-3.8-flash", sleep=lambda s: None)
    assert generator.generate("Summarize") == "Growth slows."
    assert models.calls[0].model == "gemini-3.8-flash"
    assert models.calls[0].config.temperature == 0.2


def test_generator_rejects_empty_answers():
    from gemini import Generator

    generator = Generator(SimpleNamespace(models=FakeGenModels(texts=[None])), "m", sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="no text"):
        generator.generate("x")


def test_generator_waits_out_rate_limits_and_asks_the_limiter():
    from gemini import Generator

    sleeps, asked = [], []
    models = FakeGenModels(failures=[FakeApiError(429)])
    generator = Generator(SimpleNamespace(models=models), "m", sleep=sleeps.append, limiter=SimpleNamespace(acquire=asked.append))
    assert generator.generate("x") == "A summary."
    assert sleeps == [15.0]
    assert asked == [1]
```

Append to `ingest/tests/test_config.py`:

```python


def test_chat_rate_limit_defaults_to_ten_and_rejects_nonsense():
    assert load_settings(VALID).chat_per_minute == 10
    assert load_settings({**VALID, "GEMINI_CHAT_REQUESTS_PER_MINUTE": "60"}).chat_per_minute == 60
    with pytest.raises(ConfigError, match="GEMINI_CHAT_REQUESTS_PER_MINUTE"):
        load_settings({**VALID, "GEMINI_CHAT_REQUESTS_PER_MINUTE": "0"})
```

Append to `ingest/tests/test_gemini_live.py`:

```python


def test_real_generation_answers_from_the_prompt():
    from gemini import make_generator

    answer = make_generator(load_settings()).generate(
        "Growth in South Asia is projected at 6.3 percent in 2026. Reply with only the 2026 growth projection."
    )
    assert "6.3" in answer
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_gemini.py tests/test_config.py -q -p no:warnings -k "generator or chat_rate"`
Expected: 4 failed. Three with `ImportError: cannot import name 'Generator'`, and one with `AttributeError: 'Settings' object has no attribute 'chat_per_minute'`.

- [ ] **Step 3: Add the setting.** In `ingest/config.py`, replace

```python
    embed_per_minute: int = 100  # Gemini free tier counts each embedded text as one request
```

with

```python
    embed_per_minute: int = 100  # Gemini free tier counts each embedded text as one request
    chat_per_minute: int = 10  # generation calls (section summaries); raise on a paid plan
```

replace

```python
    per_minute_raw = env.get("EMBED_REQUESTS_PER_MINUTE", "").strip() or "100"
    if not per_minute_raw.isdigit() or int(per_minute_raw) < 1:
        raise ConfigError(f"EMBED_REQUESTS_PER_MINUTE must be a positive integer, got {per_minute_raw!r}")
```

with

```python
    embed_per_minute = _positive_int(env, "EMBED_REQUESTS_PER_MINUTE", 100)
    chat_per_minute = _positive_int(env, "GEMINI_CHAT_REQUESTS_PER_MINUTE", 10)
```

and replace

```python
        embed_per_minute=int(per_minute_raw),
    )
```

with

```python
        embed_per_minute=embed_per_minute,
        chat_per_minute=chat_per_minute,
    )


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip() or str(default)
    if not raw.isdigit() or int(raw) < 1:
        raise ConfigError(f"{name} must be a positive integer, got {raw!r}")
    return int(raw)
```

- [ ] **Step 4: Add the generator.** Append to `ingest/gemini.py`:

```python


class Generator:
    """Text generation for ingest-time enrichment (section summaries)."""

    def __init__(self, client, model: str, sleep: Callable[[float], None] = time.sleep, limiter: RateLimiter | None = None):
        self._client = client
        self._model = model
        self._sleep = sleep
        self._limiter = limiter

    def generate(self, prompt: str) -> str:
        if self._limiter is not None:
            self._limiter.acquire(1)
        config = types.GenerateContentConfig(temperature=0.2)
        response = with_retry(
            lambda: self._client.models.generate_content(model=self._model, contents=prompt, config=config),
            sleep=self._sleep,
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError(f"{self._model} returned no text")
        return text


def make_generator(settings: Settings) -> Generator:
    return Generator(make_client(settings), settings.chat_model, limiter=RateLimiter(settings.chat_per_minute))
```

- [ ] **Step 5: Document the setting.** In `.env.example` replace

```dotenv
# EMBED_REQUESTS_PER_MINUTE=100
```

with

```dotenv
# EMBED_REQUESTS_PER_MINUTE=100
# Optional: generation calls per minute for section summaries (raise on a paid plan)
# GEMINI_CHAT_REQUESTS_PER_MINUTE=10
```

- [ ] **Step 6: Run the tests**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 149 passed, 2 deselected

Run: `cd ingest && uv run pytest -m live -q -p no:warnings`
Expected: 2 passed. If generation fails with HTTP 404, set `GEMINI_CHAT_MODEL` in `.env` to a current Flash model ID and re-run.

- [ ] **Step 7: Commit**

```bash
git add ingest/config.py ingest/gemini.py .env.example ingest/tests/test_gemini.py ingest/tests/test_config.py ingest/tests/test_gemini_live.py
git commit -m "Add rate-limited text generation"
```

---

### Task 3: Section summaries hook

**Files:**
- Modify: `ingest/chunk/common.py` (add `truncate_tokens`)
- Create: `ingest/enrich/__init__.py` (empty), `ingest/enrich/summaries.py`
- Modify: `ingest/pipeline.py` (`DocSpec.summaries`, `Summarizer`, `run(..., summarizer=None)`)
- Modify: `ingest/ingest.py` (wire the summarizer)
- Test: `ingest/tests/test_summaries.py`, `ingest/tests/test_pipeline_db.py`

**Interfaces:**
- Consumes: `cache.JsonCache`, `chunk.common.count_tokens`, `model.SectionDraft`, `model.ChunkDraft`, `gemini.make_generator` (Task 2).
- Produces:
  - `chunk.common.truncate_tokens(text: str, max_tokens: int) -> str`
  - `enrich.summaries.PROMPT_VERSION = "v1"`, `MIN_TOKENS = 300`, `MAX_INPUT_TOKENS = 6000`, `PROMPT`
  - `enrich.summaries.subtree_texts(sections) -> dict[str, str]`
  - `enrich.summaries.summary_chunks(doc_title, sections, generator, cache: JsonCache, model: str) -> list[ChunkDraft]`
  - `pipeline.DocSpec.summaries: bool = False`; `pipeline.Summarizer = Callable[[DocSpec, list[SectionDraft]], list[ChunkDraft]]`; `pipeline.run(spec, profile, settings, conn, embedder, cache_root, summarizer=None)`. Summary chunks are added only when `spec.summaries` is true and a summarizer is given.
  - `ingest.py` builds a summarizer that caches in `ingest/.cache/<slug>/summaries/`.

- [ ] **Step 1: Write the failing tests.** Create `ingest/tests/test_summaries.py`:

```python
from cache import JsonCache
from chunk.common import count_tokens, truncate_tokens
from enrich.summaries import summary_chunks, subtree_texts
from model import SectionDraft

LONG = "Growth in South Asia is projected to slow. " * 60


def tree():
    return [
        SectionDraft("root", "GEP", 0, "", 1, 200),
        SectionDraft("ch2", "Ch. 2 Regional Outlooks", 1, "Ch. 2 Regional Outlooks", 69, 106, "root"),
        SectionDraft("sar", "South Asia", 2, "Ch. 2 Regional Outlooks › South Asia", 96, 98, "ch2", "region"),
        SectionDraft("out", "Outlook", 3, "Ch. 2 Regional Outlooks › South Asia › Outlook", 96, 97, "sar", content=LONG),
        SectionDraft("eap", "East Asia and Pacific", 2, "Ch. 2 Regional Outlooks › East Asia and Pacific", 81, 83, "ch2", "region", "Short text."),
    ]


class FakeGenerator:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return f"summary {len(self.prompts)}"


def test_subtree_text_includes_descendants_in_order():
    texts = subtree_texts(tree())
    assert texts["sar"] == LONG
    assert texts["ch2"] == f"{LONG}\n\nShort text."


def test_only_long_level_one_and_two_sections_are_summarized(tmp_path):
    generator = FakeGenerator()
    chunks = summary_chunks("GEP June 2026", tree(), generator, JsonCache(tmp_path, "gep/summaries"), "m")
    assert [(c.section_key, c.kind, c.page_start, c.page_end) for c in chunks] == [
        ("ch2", "section_summary", 69, 106),
        ("sar", "section_summary", 96, 98),
    ]
    assert chunks[1].content == "Summary of Ch. 2 Regional Outlooks › South Asia: summary 2"


def test_prompt_names_the_report_and_section_and_forbids_outside_facts(tmp_path):
    generator = FakeGenerator()
    summary_chunks("GEP June 2026", tree(), generator, JsonCache(tmp_path, "gep/summaries"), "m")
    prompt = generator.prompts[1]
    assert '"GEP June 2026"' in prompt
    assert "Section: Ch. 2 Regional Outlooks › South Asia" in prompt
    assert "using only facts stated in the text" in prompt


def test_cached_summaries_cost_nothing_on_rerun(tmp_path):
    cache = JsonCache(tmp_path, "gep/summaries")
    summary_chunks("GEP", tree(), FakeGenerator(), cache, "m")
    again = FakeGenerator()
    summary_chunks("GEP", tree(), again, cache, "m")
    assert again.prompts == []


def test_truncate_tokens():
    text = "word " * 50
    assert count_tokens(truncate_tokens(text, 10)) <= 10
    assert truncate_tokens("short text", 10) == "short text"
```

Append to `ingest/tests/test_pipeline_db.py`:

```python


def test_summaries_are_added_only_when_the_spec_asks(conn, tmp_path):
    from model import ChunkDraft

    settings = load_settings()
    calls = []

    def summarizer(spec, sections):
        calls.append(spec.slug)
        root = next(s for s in sections if s.level == 0)
        return [ChunkDraft(root.key, "section_summary", "Summary of iMac: recycled aluminum stand.", 1, 12)]

    spec = replace(DOCS["imac"], slug="test-imac")
    run(spec, plain.build, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path, summarizer)
    assert calls == []
    report = run(replace(spec, summaries=True), plain.build, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path, summarizer)
    assert calls == ["test-imac"]
    assert report.chunks_by_kind["section_summary"] == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_summaries.py tests/test_pipeline_db.py -q -p no:warnings`
Expected: FAIL. `ImportError: cannot import name 'truncate_tokens'` while collecting `test_summaries.py`, and `TypeError: run() takes 6 positional arguments but 7 were given`.

- [ ] **Step 3: Add `truncate_tokens`.** In `ingest/chunk/common.py`, replace

```python
def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))
```

with

```python
def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def truncate_tokens(text: str, max_tokens: int) -> str:
    tokens = _encoding().encode(text)
    return text if len(tokens) <= max_tokens else _encoding().decode(tokens[:max_tokens])
```

- [ ] **Step 4: Write `ingest/enrich/__init__.py` (empty) and `ingest/enrich/summaries.py`**

```python
"""Section summaries (spec §5.3): a short Gemini summary per major section, indexed as its own chunk.

Broad questions ("What does Chapter 3 say about sovereign debt?") match a summary better than any
single 350-token passage.
"""
from __future__ import annotations

from collections import defaultdict

from cache import JsonCache
from chunk.common import count_tokens, truncate_tokens
from model import ChunkDraft, SectionDraft

PROMPT_VERSION = "v1"
MIN_TOKENS = 300
MAX_INPUT_TOKENS = 6000
PROMPT = """You summarize one section of the report "{doc}".
Write 3-5 sentences using only facts stated in the text below. Keep key figures with their units and years.
Do not add outside knowledge or opinions.

Section: {path}

{text}"""


def subtree_texts(sections: list[SectionDraft]) -> dict[str, str]:
    """Each section's own text followed by its descendants' text, in document order."""
    children: dict[str, list[SectionDraft]] = defaultdict(list)
    for section in sections:
        if section.parent_key:
            children[section.parent_key].append(section)

    def text(section: SectionDraft) -> str:
        parts = [section.content or ""] + [text(child) for child in children[section.key]]
        return "\n\n".join(p for p in parts if p)

    return {section.key: text(section) for section in sections}


def summary_chunks(doc_title: str, sections: list[SectionDraft], generator, cache: JsonCache, model: str) -> list[ChunkDraft]:
    texts = subtree_texts(sections)
    chunks = []
    for section in sections:
        text = texts[section.key]
        if not 1 <= section.level <= 2 or count_tokens(text) < MIN_TOKENS:
            continue
        prompt = PROMPT.format(doc=doc_title, path=section.path, text=truncate_tokens(text, MAX_INPUT_TOKENS))
        key = JsonCache.key(model, PROMPT_VERSION, prompt)
        summary = cache.get(key)
        if summary is None:
            summary = generator.generate(prompt)
            cache.put(key, summary)
        chunks.append(
            ChunkDraft(section.key, "section_summary", f"Summary of {section.path}: {summary}", section.page_start, section.page_end)
        )
    return chunks
```

- [ ] **Step 5: Add the pipeline hook.** In `ingest/pipeline.py`, replace

```python
    skip_pages: frozenset[int] = frozenset()
    endnote_pages: frozenset[int] = frozenset()
```

with

```python
    skip_pages: frozenset[int] = frozenset()
    endnote_pages: frozenset[int] = frozenset()
    summaries: bool = False  # add a Gemini summary chunk per major section (spec §5.3)
```

replace

```python
Profile = Callable[[DocSpec, Prepared], BuildResult]
```

with

```python
Profile = Callable[[DocSpec, Prepared], BuildResult]
Summarizer = Callable[[DocSpec, list[SectionDraft]], list[ChunkDraft]]
```

and replace

```python
def run(spec: DocSpec, profile: Profile, settings: Settings, conn, embedder, cache_root: Path) -> Report:
    report = Report(spec.slug, spec.title)
    prepared = prepare(spec)
    result = profile(spec, prepared)
    cited = attach_footnotes(result.chunks, prepared.notes, report)
    chunks = result.chunks + note_chunks(prepared.notes.notes, result.sections, cited)
```

with

```python
def run(
    spec: DocSpec, profile: Profile, settings: Settings, conn, embedder, cache_root: Path, summarizer: Summarizer | None = None
) -> Report:
    report = Report(spec.slug, spec.title)
    prepared = prepare(spec)
    result = profile(spec, prepared)
    cited = attach_footnotes(result.chunks, prepared.notes, report)
    chunks = result.chunks + note_chunks(prepared.notes.notes, result.sections, cited)
    if spec.summaries and summarizer is not None:
        chunks += summarizer(spec, result.sections)
```

- [ ] **Step 6: Wire the CLI.** In `ingest/ingest.py`, replace

```python
from gemini import make_embedder
```

with

```python
from enrich.summaries import summary_chunks
from gemini import make_embedder, make_generator
```

replace

```python
    embedder = make_embedder(settings)
    with connect(settings) as conn:
```

with

```python
    embedder = make_embedder(settings)
    generator = make_generator(settings)

    def summarizer(spec, sections):
        cache = JsonCache(CACHE_ROOT, f"{spec.slug}/summaries")
        return summary_chunks(spec.title, sections, generator, cache, settings.chat_model)

    with connect(settings) as conn:
```

and replace

```python
            report = run(spec, PROFILES[spec.profile], settings, conn, embedder, CACHE_ROOT)
```

with

```python
            report = run(spec, PROFILES[spec.profile], settings, conn, embedder, CACHE_ROOT, summarizer)
```

- [ ] **Step 7: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 155 passed, 2 deselected

- [ ] **Step 8: Commit**

```bash
git add ingest/chunk/common.py ingest/enrich/__init__.py ingest/enrich/summaries.py ingest/pipeline.py ingest/ingest.py ingest/tests/test_summaries.py ingest/tests/test_pipeline_db.py
git commit -m "Add section summaries hook"
```

---

### Task 4: Classify GEP lines, figures and tables

**Files:**
- Create: `ingest/chunk/hierarchical.py` (everything before `class _Outline`; `build` comes in Task 5)
- Modify: `ingest/tests/conftest.py` (add `gep_prepared`)
- Test: `ingest/tests/test_hierarchical_lines.py`

**Interfaces:**
- Consumes: `model.Line`, `model.Span` (with `font`, `color`), `model.Page`; `parse.tables.merge_symbol_cells`, `pad_numeric_rows`, `region_rows`, `split_table`; `chunk.common.count_tokens`; `pipeline.prepare`; `profiles.DOCS`.
- Produces (`chunk.hierarchical`):
  - `TARGET_TOKENS = 350`, `OVERLAP_TOKENS = 50`, `TABLE_TOKENS = 400`
  - `GepLayout` frozen dataclass with `front_title`, `section_font`, `section_sizes`, `subsection`, `minor`, `object_title`, `box_body_size`, `chart_text`, `note_text`, `annex_table_pages`, `regions`, `excluded_titles`; `GEP_LAYOUT = GepLayout()`
  - `line_role(line, layout) -> str`, one of `front | section | subsection | minor | object | graphic | box_text | text`
  - `chapter_starts(pdf_path) -> dict[int, str]`
  - `figure_text(title, lines, layout) -> str`
  - `table_pieces(title, lines, page, layout) -> tuple[list[str], str]`, returning markdown pieces and a notes text (`""` if none)
  - session fixture `gep_prepared` (= `prepare(DOCS["gep"])`)

- [ ] **Step 1: Add the fixture.** Append to `ingest/tests/conftest.py`:

```python


@pytest.fixture(scope="session")
def gep_prepared():
    from pipeline import prepare
    from profiles import DOCS

    return prepare(DOCS["gep"])
```

- [ ] **Step 2: Write the failing tests.** Create `ingest/tests/test_hierarchical_lines.py`:

```python
import pytest

from chunk.hierarchical import GEP_LAYOUT, chapter_starts, figure_text, line_role, table_pieces
from helpers import page_of
from model import Line, Span
from profiles import DOCS


def first(page, prefix):
    return next(l for l in page.lines if l.text.startswith(prefix))


@pytest.mark.parametrize(
    "page, prefix, role",
    [
        (19, "Executive Summary", "front"),
        (25, "Summary", "section"),
        (103, "References", "section"),
        (30, "From task-level gains", "subsection"),
        (52, "Downside risks", "minor"),
        (27, "FIGURE 1.1", "object"),
        (27, "Global growth is expected to slow in 2026", "graphic"),
        (19, "The global economy", "text"),
    ],
)
def test_line_roles(gep_prepared, page, prefix, role):
    assert line_role(first(page_of(gep_prepared.pages, page), prefix), GEP_LAYOUT) == role


def test_box_text_is_the_smaller_body_size(gep_prepared):
    line = first(page_of(gep_prepared.pages, 31), "Most studies focus on the United States")
    assert line_role(line, GEP_LAYOUT) == "box_text"


def test_chapter_starts_come_from_bookmarks():
    assert chapter_starts(DOCS["gep"].path) == {
        23: "Ch. 1 Global Outlook",
        69: "Ch. 2 Regional Outlooks",
        107: "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs",
        143: "Ch. 4 Navigating Volatility: Fiscal Policy and Commodity Price Swings",
        185: "Statistical Annex",
    }


def styled(text, font, size):
    return Line((Span(text, size, 4, (0, 0, 1, 1), font, 0),), (0, 0, 1, 1), 0)


def test_figure_text_keeps_takeaway_panels_and_sources_but_not_ticks():
    lines = [
        styled("Growth slows in 2026.", "HelveticaLTStd-Obl", 8.0),
        styled("A. Share of economies by 2026", "HelveticaLTStd-Bold", 7.0),
        styled("forecast revision", "HelveticaLTStd-Bold", 7.0),
        styled("B. Energy prices", "HelveticaLTStd-Bold", 7.0),
        styled("2.5", "Helvetica", 5.7),
        styled("Sources: World Bank.", "HelveticaLTStd-Roman", 6.0),
    ]
    assert figure_text("FIGURE 1.1 Global economic prospects", lines, GEP_LAYOUT) == (
        "FIGURE 1.1 Global economic prospects\n\nGrowth slows in 2026.\n\n"
        "Panels: A. Share of economies by 2026 forecast revision; B. Energy prices\n\nSources: World Bank."
    )


def test_south_asia_forecast_table(gep_prepared):
    page = page_of(gep_prepared.pages, 98)
    top, below = first(page, "TABLE 2.9"), first(page, "TABLE 2.10")
    members = [
        l for l in page.lines if line_role(l, GEP_LAYOUT) == "graphic" and top.bbox[1] - 1 <= l.bbox[1] < below.bbox[1] - 1
    ]
    pieces, notes = table_pieces("TABLE 2.9 South Asia forecast summary", members, page, GEP_LAYOUT)
    assert len(pieces) >= 2
    assert all(p.startswith("TABLE 2.9 South Asia forecast summary\n\n") for p in pieces)
    assert all("|  | 2023 | 2024 | 2025e | 2026f | 2027f | 2028f | 2026f | 2027f |" in p for p in pieces)
    assert "| EMDE SAR, GDP | 6.6 | 6.8 | 7.0 | 6.3 | 6.9 | 7.1 | 0.1 | 0.4 |" in pieces[0]
    assert notes.startswith("TABLE 2.9 South Asia forecast summary\n\n")
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_hierarchical_lines.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'chunk.hierarchical'`

- [ ] **Step 4: Write `ingest/chunk/hierarchical.py`**

```python
"""Hierarchical profile for the World Bank Global Economic Prospects report (spec §5.3)."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import pymupdf

from chunk.common import count_tokens, pack, paragraphs_from_pages
from model import ChunkDraft, Line, Page, SectionDraft, Span
from parse.tables import merge_symbol_cells, pad_numeric_rows, region_rows, split_table
from pipeline import BuildResult, DocSpec, Prepared

TARGET_TOKENS = 350
OVERLAP_TOKENS = 50
TABLE_TOKENS = 400
_OBJECT = re.compile(r"(FIGURE|TABLE|BOX)\s*(B?\d+(?:\.\d+)+)")
_PANEL = re.compile(r"[A-F]\.\s")


@dataclass(frozen=True)
class GepLayout:
    """Typography observed in Global Economic Prospects, June 2026."""

    front_title: tuple[str, float] = ("HelveticaLTStd-Cond", 26.0)  # Acknowledgments, Foreword, Executive Summary…
    section_font: str = "HelveticaLTStd-Bold"
    section_sizes: tuple[float, ...] = (16.0, 14.0)  # "Summary", "South Asia", "ANNEX 3.1 …"; Ch. 2 "References" is 14pt
    subsection: tuple[str, float, int] = ("HelveticaLTStd-Roman", 11.0, 0x4475A1)  # "Outlook", box sub-heads
    minor: tuple[str, float, int] = ("AGaramondPro-BoldItalic", 11.0, 0x4475A1)  # "Downside risks"
    object_title: tuple[str, float] = ("HelveticaLTStd-Bold", 10.0)  # FIGURE / TABLE / BOX x.y titles
    box_body_size: float = 10.6  # box text; chapter text is 11pt
    chart_text: frozenset[tuple[str, float]] = frozenset(
        {("HelveticaLTStd-Obl", 8.0), ("HelveticaLTStd-Bold", 7.0), ("HelveticaLTStd-Roman", 6.0)}
    )
    note_text: tuple[str, float] = ("HelveticaLTStd-Roman", 6.0)  # Sources / Note lines under figures and tables
    annex_table_pages: frozenset[int] = frozenset({187, 188, 189, 190})
    regions: tuple[str, ...] = (
        "East Asia and Pacific",
        "Europe and Central Asia",
        "Latin America and the Caribbean",
        "Middle East, North Africa, Afghanistan, and Pakistan",
        "South Asia",
        "Sub-Saharan Africa",
    )
    excluded_titles: frozenset[str] = frozenset({"References"})


GEP_LAYOUT = GepLayout()


def _main(line: Line) -> Span | None:
    spans = [s for s in line.spans if s.text.strip()]
    return max(spans, key=lambda s: len(s.text.strip())) if spans else None


def _is(span: Span | None, font: str, size: float, color: int | None = None) -> bool:
    return span is not None and span.font == font and round(span.size, 1) == size and (color is None or span.color == color)


def line_role(line: Line, layout: GepLayout) -> str:
    """front | section | subsection | minor | object | graphic | box_text | text"""
    m = _main(line)
    if m is None:
        return "text"
    if _is(m, *layout.front_title):
        return "front"
    if m.font == layout.section_font and round(m.size, 1) in layout.section_sizes:
        return "section"
    if _is(m, *layout.subsection):
        return "subsection"
    if _is(m, *layout.minor):
        return "minor"
    if _is(m, *layout.object_title) and _OBJECT.match(line.text):
        return "object"
    if m.font.startswith("Helvetica"):
        return "graphic"
    if abs(m.size - layout.box_body_size) < 0.05:
        return "box_text"
    return "text"


def chapter_starts(pdf_path: Path) -> dict[int, str]:
    """Chapter-level bookmarks ("Ch. 1 Global Outlook", "Statistical Annex") by start page."""
    with pymupdf.open(pdf_path) as doc:
        toc = doc.get_toc()
    starts = {}
    for _, title, page in toc:
        title = re.sub(r"\s+", " ", title).strip()
        if title.startswith("Ch. ") or title == "Statistical Annex":
            starts[page] = title
    return starts


def figure_text(title: str, lines: list[Line], layout: GepLayout) -> str:
    """Title, the italic takeaway, panel titles and sources; chart tick labels are dropped."""
    summary, panels, notes = [], [], []
    for line in lines:
        m = _main(line)
        key = (m.font, round(m.size, 1)) if m else None
        if key == ("HelveticaLTStd-Obl", 8.0):
            summary.append(line.text)
        elif key == ("HelveticaLTStd-Bold", 7.0):
            if _PANEL.match(line.text) or not panels:
                panels.append(line.text)
            else:
                panels[-1] += " " + line.text
        elif key == layout.note_text:
            notes.append(line.text)
    parts = [title, " ".join(summary)]
    if panels:
        parts.append("Panels: " + "; ".join(panels))
    parts.append(" ".join(notes))
    return "\n\n".join(p for p in parts if p)


def table_pieces(title: str, lines: list[Line], page: Page, layout: GepLayout) -> tuple[list[str], str]:
    cells = [l for l in lines if (_main(l).font, round(_main(l).size, 1)) != layout.note_text]
    notes = " ".join(l.text for l in lines if (_main(l).font, round(_main(l).size, 1)) == layout.note_text)
    rows = pad_numeric_rows(merge_symbol_cells(region_rows(replace(page, lines=tuple(cells)), (0, 0, page.width, page.height))))
    if rows and rows[0] and rows[0][0].replace(" ", "").startswith(title.replace(" ", "")[:12]):
        rows = rows[1:]  # the title itself was captured as the first row
    header_rows = next((i + 1 for i, row in enumerate(rows) if "2026f" in row), 1)
    budget = TABLE_TOKENS - count_tokens(title) - 10
    pieces = [f"{title}\n\n{md}" for md in split_table(rows, header_rows, budget, count_tokens)]
    return pieces, (f"{title}\n\n{notes}" if notes else "")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_hierarchical_lines.py -q -p no:warnings`
Expected: 12 passed

- [ ] **Step 6: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 167 passed, 2 deselected

- [ ] **Step 7: Commit**

```bash
git add ingest/chunk/hierarchical.py ingest/tests/conftest.py ingest/tests/test_hierarchical_lines.py
git commit -m "Classify GEP lines, figures and tables"
```

---

### Task 5: Build the GEP section tree

**Files:**
- Modify: `ingest/chunk/hierarchical.py` (append `_Outline` and `build`)
- Modify: `ingest/profiles.py` (register `hierarchical`; GEP skip pages and summaries)
- Test: `ingest/tests/test_hierarchical_build.py`

**Interfaces:**
- Consumes: Task 4 helpers; `chunk.common.pack`, `paragraphs_from_pages`; `pipeline.BuildResult`, `DocSpec`, `Prepared`, `prepare`, `attach_footnotes`; `report.Report`.
- Produces:
  - `chunk.hierarchical.build(spec, prepared, layout=GEP_LAYOUT) -> BuildResult`. Section kinds are `front | section | region | box | annex`; chunk kinds are `text | chart | table | caption`.
  - `profiles.DOCS["gep"]` with `profile="hierarchical"`, `skip_pages=frozenset(range(1, 15)) | frozenset(range(192, 201))` and `summaries=True`
  - `profiles.PROFILES["hierarchical"] = hierarchical.build`

- [ ] **Step 1: Write the failing tests.** Create `ingest/tests/test_hierarchical_build.py`:

```python
import pytest

from chunk.common import count_tokens
from chunk.hierarchical import build
from pipeline import attach_footnotes, prepare
from profiles import DOCS, PROFILES
from report import Report

CH2 = "Ch. 2 Regional Outlooks"


@pytest.fixture(scope="module")
def built():
    prepared = prepare(DOCS["gep"])
    result = build(DOCS["gep"], prepared)
    return result, {s.key: s for s in result.sections}, prepared


def chunks_with(built, needle):
    result, by_key, _ = built
    return [(c.kind, by_key[c.section_key].path) for c in result.chunks if needle in c.content]


def test_gep_uses_the_hierarchical_profile_with_summaries():
    spec = DOCS["gep"]
    assert (spec.profile, spec.summaries) == ("hierarchical", True)
    assert spec.skip_pages == frozenset(range(1, 15)) | frozenset(range(192, 201))
    assert "hierarchical" in PROFILES


def test_level_one_sections(built):
    assert [s.title for s in built[0].sections if s.level == 1] == [
        "Acknowledgments",
        "Foreword",
        "Executive Summary",
        "Abbreviations",
        "Ch. 1 Global Outlook",
        CH2,
        "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs",
        "Ch. 4 Navigating Volatility: Fiscal Policy and Commodity Price Swings",
        "Statistical Annex",
    ]


def test_six_regions_with_their_real_pages(built):
    regions = [(s.title, s.page_start, s.page_end, s.path) for s in built[0].sections if s.kind == "region"]
    assert [(t, a, b) for t, a, b, _ in regions] == [
        ("East Asia and Pacific", 81, 83),
        ("Europe and Central Asia", 84, 87),
        ("Latin America and the Caribbean", 88, 91),
        ("Middle East, North Africa, Afghanistan, and Pakistan", 92, 95),
        ("South Asia", 96, 98),
        ("Sub-Saharan Africa", 99, 102),
    ]
    assert all(path.startswith(f"{CH2} › ") for *_, path in regions)


def test_boxes_and_their_own_text(built):
    boxes = [s.title for s in built[0].sections if s.kind == "box"]
    assert boxes == [
        "BOX 1.1 How much will AI affect global growth?",
        "BOX 1.2 Unlocking private investment",
        "BOX 2.1 Low-income countries: Recent developments and outlook",
        "BOX 3.1 The costs of hidden debt",
    ]
    box_path = "Ch. 1 Global Outlook › BOX 1.1 How much will AI affect global growth?"
    assert f"{box_path} › From task-level gains to economy-wide productivity" in [s.path for s in built[0].sections]
    assert all(path.startswith(box_path) for kind, path in chunks_with(built, "Most studies focus on the United States"))


def test_south_asia_outlook_text_table_and_figure(built):
    sar = f"{CH2} › South Asia"
    assert ("text", f"{sar} › Outlook") in chunks_with(built, "Growth in SAR is expected to soften to 6.3 percent in 2026")
    tables = [p for k, p in chunks_with(built, "TABLE 2.9 South Asia forecast summary") if k == "table"]
    assert tables and all(p.startswith(sar) for p in tables)
    assert [k for k, p in chunks_with(built, "FIGURE 2.7 SAR: Outlook") if p.startswith(sar)] == ["chart"]


def test_wrapped_headings_merge_at_every_level(built):
    paths = [s.path for s in built[0].sections]
    assert "Ch. 1 Global Outlook › Major economies: Recent developments and outlook" in paths
    assert (
        "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs › Impact of EMDE government debt on "
        "interest rates › Non-linear relationship between debt and interest rates"
    ) in paths
    assert not {"interest rates", "the Caribbean", "and data"} & {s.title for s in built[0].sections}


def test_references_are_in_the_tree_but_not_chunked(built):
    result, by_key, _ = built
    references = [s for s in result.sections if s.title == "References"]
    assert len(references) == 4
    assert not [c for c in result.chunks if by_key[c.section_key].title == "References"]


def test_statistical_annex_pages_are_tables(built):
    annex = [k for k, p in chunks_with(built, "| World | 2.8 | 2.9 | 2.9 | 2.5 | 2.8 | 2.8 | 3.1 |")]
    assert annex == ["table"]
    result, by_key, _ = built
    assert sum(by_key[c.section_key].path == "Statistical Annex › Real GDP growth" and c.kind == "table" for c in result.chunks) >= 4


def test_executive_summary_text(built):
    assert ("text", "Executive Summary") in chunks_with(built, "The global economy is facing another major shock")


def test_every_page_footnote_reaches_a_body_chunk(built):
    result, _, prepared = built
    attach_footnotes(result.chunks, prepared.notes, Report("gep", "GEP"))
    cited = {(f["n"], f["text"]) for c in result.chunks for f in c.footnotes}
    assert cited == {(n.n, n.text) for n in prepared.notes.notes}
    assert not [c for c in result.chunks if c.kind == "table" and c.refs]


def test_chunks_stay_inside_their_sections_and_budgets(built):
    result, by_key, _ = built
    for chunk in result.chunks:
        section = by_key[chunk.section_key]
        assert section.page_start <= chunk.page_start <= chunk.page_end <= section.page_end
        assert count_tokens(chunk.content) <= 1000
    kinds = [c.kind for c in result.chunks]
    assert kinds.count("text") >= 300 and kinds.count("table") >= 120 and kinds.count("chart") >= 50
    assert len(result.sections) >= 130
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_hierarchical_build.py -q -p no:warnings`
Expected: FAIL with `ImportError: cannot import name 'build' from 'chunk.hierarchical'`

- [ ] **Step 3: Add `_Outline` and `build`.** Append to `ingest/chunk/hierarchical.py`:

```python


class _Outline:
    """The section tree as headings arrive in reading order, plus the body lines filed under it."""

    def __init__(self, root: SectionDraft, layout: GepLayout):
        self.layout = layout
        self.root = root
        self.sections: list[SectionDraft] = [root]
        self.by_key = {"root": root}
        self.body: dict[str, list[tuple[int, Line]]] = defaultdict(list)
        self.boxes: dict[str, SectionDraft] = {}
        self.l1, self.l2, self.l3, self.l4 = root, None, None, None
        self.box: SectionDraft | None = None
        self.box_leaf: SectionDraft | None = None
        self.last: tuple[SectionDraft, Line, str] | None = None  # heading just opened, until body text follows

    def add(self, level: int, title: str, page_no: int, parent: SectionDraft, kind: str = "section") -> SectionDraft:
        path = title if parent is self.root else f"{parent.path} › {title}"
        section = SectionDraft(f"s{len(self.sections)}", title, level, path, page_no, page_no, parent.key, kind)
        self.sections.append(section)
        self.by_key[section.key] = section
        return section

    def chapter_leaf(self) -> SectionDraft:
        return self.l4 or self.l3 or self.l2 or self.l1

    def open_l1(self, title: str, page_no: int, kind: str) -> None:
        self.l1, self.l2, self.l3, self.l4 = self.add(1, title, page_no, self.root, kind), None, None, None
        self.box = self.box_leaf = None
        self.last = None

    def _continues(self, line: Line, role: str) -> bool:
        """A wrapped heading: same role, directly below the line just opened."""
        return self.last is not None and self.last[2] == role and 0 <= line.bbox[1] - self.last[1].bbox[3] < 0.6 * line.size

    def _extend(self, line: Line, role: str) -> None:
        section = self.last[0]
        section.title = f"{section.title} {line.text}"
        parent = self.by_key[section.parent_key]
        section.path = section.title if parent is self.root else f"{parent.path} › {section.title}"
        if section.title in self.layout.regions:
            section.kind = "region"
        self.last = (section, line, role)

    def section_heading(self, line: Line, page_no: int) -> None:
        title = line.text
        if self._continues(line, "section") and not title.startswith("ANNEX "):
            return self._extend(line, "section")
        if self.l2 is not None and title.endswith(" (continued)") and self.l2.title == title.removesuffix(" (continued)"):
            self.last = (self.l2, line, "section")
            return
        kind = "region" if title in self.layout.regions else "annex" if title.startswith("ANNEX ") else "section"
        self.l2, self.l3, self.l4 = self.add(2, title, page_no, self.l1, kind), None, None
        self.box = self.box_leaf = None
        self.last = (self.l2, line, "section")

    def box_title(self, line: Line, number: str, page_no: int) -> None:
        box = self.boxes.get(number)
        if box is None:
            box = self.boxes[number] = self.add(2, line.text, page_no, self.l1, "box")
        if self.box is not box:  # later pages of a box repeat its title as a running header
            self.box = self.box_leaf = box
        self.last = None

    def sub_heading(self, line: Line, role: str, page_no: int, next_body_role: str | None) -> None:
        if self._continues(line, role):
            return self._extend(line, role)
        if role == "subsection" and self.box is not None and next_body_role == "box_text":
            self.box_leaf = opened = self.add(3, line.text, page_no, self.box)
        elif role == "subsection":
            parent = self.l2 or self.l1
            self.l3, self.l4 = self.add(parent.level + 1, line.text, page_no, parent), None
            opened = self.l3
        else:
            parent = self.l3 or self.l2 or self.l1
            self.l4 = opened = self.add(parent.level + 1, line.text, page_no, parent)
        self.last = (opened, line, role)

    def body_line(self, line: Line, role: str, page_no: int) -> None:
        target = self.box_leaf if role == "box_text" and self.box is not None else self.chapter_leaf()
        self.body[target.key].append((page_no, line))
        self.last = None


def build(spec: DocSpec, prepared: Prepared, layout: GepLayout = GEP_LAYOUT) -> BuildResult:
    chapters = chapter_starts(spec.path)
    pages = prepared.pages
    outline = _Outline(SectionDraft("root", spec.title, 0, "", pages[0].number, pages[-1].number), layout)
    chunks: list[ChunkDraft] = []

    for page in pages:
        if page.number in chapters:
            outline.open_l1(chapters[page.number], page.number, "annex" if chapters[page.number] == "Statistical Annex" else "section")
        objects: list[tuple[float, str, SectionDraft]] = []  # FIGURE/TABLE titles on this page: (y, title, section)
        graphics: list[Line] = []
        roles = [line_role(line, layout) for line in page.lines]
        for i, (line, role) in enumerate(zip(page.lines, roles)):
            if role == "front":
                outline.open_l1(line.text, page.number, "front")
            elif role == "section":
                outline.section_heading(line, page.number)
            elif role == "object":
                label, number = _OBJECT.match(line.text).groups()
                if label == "BOX":
                    outline.box_title(line, number, page.number)
                else:
                    in_box = number.startswith("B") and outline.box is not None
                    objects.append((line.bbox[1], line.text, outline.box if in_box else outline.chapter_leaf()))
            elif role in ("subsection", "minor"):
                next_body = next((r for r in roles[i + 1 :] if r in ("text", "box_text")), None)
                outline.sub_heading(line, role, page.number, next_body)
            elif role == "graphic":
                graphics.append(line)
            else:
                outline.body_line(line, role, page.number)

        def emit_table(title: str, members: list[Line], target: SectionDraft) -> None:
            pieces, notes = table_pieces(title, members, page, layout)
            chunks.extend(ChunkDraft(target.key, "table", piece, page.number, page.number) for piece in pieces)
            if notes:
                chunks.append(ChunkDraft(target.key, "caption", notes, page.number, page.number))

        if page.number in layout.annex_table_pages and outline.l2 is not None:
            emit_table(outline.l2.title, graphics, outline.l2)
            continue
        objects.sort(key=lambda o: o[0])
        for idx, (y, title, target) in enumerate(objects):  # each graphic line belongs to the title above it
            end = objects[idx + 1][0] if idx + 1 < len(objects) else float("inf")
            members = [l for l in graphics if y - 1 <= l.bbox[1] < end - 1 and l.text != title]
            if title.startswith("FIGURE"):
                chunks.append(ChunkDraft(target.key, "chart", figure_text(title, members, layout), page.number, page.number))
            else:
                emit_table(title, members, target)

    vocab = prepared.vocab or None
    for section in outline.sections:
        if section.title in layout.excluded_titles:  # bibliographies would swamp keyword search with author names
            continue
        grouped: dict[int, list[Line]] = defaultdict(list)
        for page_no, line in outline.body[section.key]:
            grouped[page_no].append(line)
        pseudo = [Page(n, pages[0].width, pages[0].height, tuple(ls)) for n, ls in sorted(grouped.items())]
        paragraphs = paragraphs_from_pages(pseudo, vocab)
        section.content = "\n\n".join(p.text for p in paragraphs) or None
        for packed in pack(paragraphs, TARGET_TOKENS, OVERLAP_TOKENS):
            chunks.append(ChunkDraft(section.key, "text", packed.text, packed.page_start, packed.page_end, refs=packed.refs))
    for chunk in chunks:  # a section spans its own and its descendants' pages
        section = outline.by_key[chunk.section_key]
        while section is not outline.root:
            section.page_start = min(section.page_start, chunk.page_start)
            section.page_end = max(section.page_end, chunk.page_end)
            section = outline.by_key[section.parent_key]
    return BuildResult(outline.sections, chunks)
```

- [ ] **Step 4: Register the profile.** In `ingest/profiles.py`, replace

```python
from chunk import narrative, plain
```

with

```python
from chunk import hierarchical, narrative, plain
```

replace

```python
    "gep": DocSpec("gep", "Global Economic Prospects, June 2026", DOCS_DIR / "GEP-Jun-2026.pdf", "plain"),
```

with

```python
    "gep": DocSpec(
        "gep",
        "Global Economic Prospects, June 2026",
        DOCS_DIR / "GEP-Jun-2026.pdf",
        "hierarchical",
        skip_pages=frozenset(range(1, 15)) | frozenset(range(192, 201)),  # cover/contents; publication lists, back cover
        summaries=True,
    ),
```

and replace

```python
PROFILES: dict[str, Profile] = {"plain": plain.build, "narrative": narrative.build}
```

with

```python
PROFILES: dict[str, Profile] = {"plain": plain.build, "narrative": narrative.build, "hierarchical": hierarchical.build}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_hierarchical_build.py -q -p no:warnings`
Expected: 11 passed

- [ ] **Step 6: Run the whole suite**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 178 passed, 2 deselected

- [ ] **Step 7: Commit**

```bash
git add ingest/chunk/hierarchical.py ingest/profiles.py ingest/tests/test_hierarchical_build.py
git commit -m "Build GEP section tree and chunks"
```

---

### Task 6: GEP validation checks and live ingest

**Files:**
- Modify: `ingest/validate.py`
- Test: `ingest/tests/test_validate.py`

**Interfaces:**
- Consumes: `validate.Check`, `chunk_with`, `section_exists`, `kind_at_least` (sub-project 3); `store.store_document` (test).
- Produces: `validate.regions_within(count: int, first: int, last: int) -> Check`, `validate.no_chunks_under(title: str) -> Check`, and `validate.DOC_CHECKS["gep"]` (the ten checks below).

- [ ] **Step 1: Write the failing tests.** Append to `ingest/tests/test_validate.py`:

```python

GEP_REGIONS = [
    ("eap", "East Asia and Pacific", 81, 83),
    ("eca", "Europe and Central Asia", 84, 87),
    ("lac", "Latin America and the Caribbean", 88, 91),
    ("mna", "Middle East, North Africa, Afghanistan, and Pakistan", 92, 95),
    ("sar", "South Asia", 96, 98),
    ("ssa", "Sub-Saharan Africa", 99, 102),
]


def store_gep_like(conn, slug, regions=GEP_REGIONS):
    settings = load_settings()
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    ch2, ch1, ch3 = "Ch. 2 Regional Outlooks", "Ch. 1 Global Outlook", "Ch. 3 A Rising Challenge: Sovereign Debt"
    sections = [
        SectionDraft("root", "GEP", 0, "", 15, 191),
        SectionDraft("es", "Executive Summary", 1, "Executive Summary", 19, 21, "root", "front"),
        SectionDraft("c1", ch1, 1, ch1, 23, 62, "root"),
        SectionDraft("b11", "BOX 1.1 How much will AI affect global growth?", 2, f"{ch1} › BOX 1.1 How much will AI affect global growth?", 30, 35, "c1", "box"),
        SectionDraft("refs", "References", 2, f"{ch1} › References", 63, 63, "c1"),
        SectionDraft("c2", ch2, 1, ch2, 69, 105, "root"),
        SectionDraft("c3", ch3, 1, ch3, 107, 134, "root"),
    ]
    sections += [SectionDraft(key, title, 2, f"{ch2} › {title}", a, b, "c2", "region") for key, title, a, b in regions]
    sections.append(SectionDraft("sar-out", "Outlook", 3, f"{ch2} › South Asia › Outlook", 96, 97, "sar"))
    chunks = [
        ChunkDraft("sar-out", "text", "Growth in SAR is expected to soften to 6.3 percent in 2026, mainly", 96, 96),
        ChunkDraft("sar-out", "table", "TABLE 2.9 South Asia forecast summary\n\n| x |", 98, 98),
        ChunkDraft("es", "text", "The global economy is facing another major shock.", 19, 19),
        ChunkDraft("c3", "section_summary", f"Summary of {ch3}: debt.", 107, 134),
    ]
    chunks += [ChunkDraft("c1", "chart", f"FIGURE 1.{i}", 27, 27) for i in range(40)]
    chunks += [ChunkDraft("c1", "table", f"TABLE 1.{i}", 26, 26) for i in range(100)]
    chunks += [ChunkDraft("c1", "section_summary", f"Summary {i}", 23, 62) for i in range(29)]
    store_document(
        conn, DocumentMeta(slug, "GEP", "hierarchical", 200), sections, chunks,
        [c.content for c in chunks], [vec] * len(chunks), settings.embed_model, settings.embed_dim,
    )


def test_gep_checks_pass_on_a_document_shaped_like_the_report(conn):
    from validate import DOC_CHECKS

    store_gep_like(conn, "test-gep")
    results = [check(conn, "test-gep") for check in DOC_CHECKS["gep"]]
    assert len(results) == 10
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_gep_checks_report_a_misplaced_region(conn):
    from validate import DOC_CHECKS

    regions = [r if r[1] != "South Asia" else ("sar", "South Asia", 95, 98) for r in GEP_REGIONS[:5]]
    store_gep_like(conn, "test-gep", regions=regions)
    failed = [r.name for r in (check(conn, "test-gep") for check in DOC_CHECKS["gep"]) if not r.ok]
    assert failed == ["6 region sections within p81–106"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ingest && uv run pytest tests/test_validate.py -q -p no:warnings -k gep`
Expected: 2 failed with `KeyError: 'gep'`

- [ ] **Step 3: Add the checks.** In `ingest/validate.py`, replace

```python
MESSAGE = "A Message from the Acting Deputy Director"
```

with

```python
def regions_within(count: int, first: int, last: int) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        rows = conn.execute(
            "SELECT s.title, s.page_start, s.page_end FROM sections s JOIN documents d ON d.id = s.doc_id "
            "WHERE d.slug = %s AND s.kind = 'region' ORDER BY s.page_start",
            (slug,),
        ).fetchall()
        inside = all(first <= a <= b <= last for _, a, b in rows)
        return CheckResult(f"{count} region sections within p{first}–{last}", len(rows) == count and inside, f"{rows}")

    return check


def no_chunks_under(title: str) -> Check:
    def check(conn: psycopg.Connection, slug: str) -> CheckResult:
        n = conn.execute(
            "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id JOIN sections s ON s.id = c.section_id "
            "WHERE d.slug = %s AND s.title = %s",
            (slug, title),
        ).fetchone()[0]
        return CheckResult(f"no chunks under {title!r}", n == 0, f"{n} found")

    return check


MESSAGE = "A Message from the Acting Deputy Director"
GEP_SAR = "Ch. 2 Regional Outlooks › South Asia"
```

and replace

```python
# Per-document checks (spec §9); gep and imac are added by sub-projects 4 and 5.
DOC_CHECKS: dict[str, list[Check]] = {
```

with

```python
# Per-document checks (spec §9); imac is added by sub-project 5.
DOC_CHECKS: dict[str, list[Check]] = {
    "gep": [
        regions_within(6, 81, 106),
        chunk_with("Growth in SAR is expected to soften to 6.3 percent in 2026", "text", f"{GEP_SAR} › Outlook"),
        chunk_with("TABLE 2.9 South Asia forecast summary", "table"),
        chunk_with("The global economy is facing another major shock", "text", "Executive Summary"),
        chunk_with("Summary of Ch. 3 A Rising Challenge", "section_summary"),
        section_exists("Ch. 1 Global Outlook › BOX 1.1 How much will AI affect global growth?"),
        kind_at_least("chart", 40),
        kind_at_least("table", 100),
        kind_at_least("section_summary", 30),
        no_chunks_under("References"),
    ],
```

- [ ] **Step 4: Run the tests**

Run: `cd ingest && uv run pytest -q -p no:warnings`
Expected: 180 passed, 2 deselected

- [ ] **Step 5: Live ingest of GEP (real Gemini embeddings and summaries).** On the free tier this takes about 10–12 minutes: ~590 texts at 100/minute, plus ~44 summaries at 10/minute. Run it in the background.

Run: `cd ingest && uv run python ingest.py --doc gep`
Expected: a report like
```
gep — Global Economic Prospects, June 2026
  pages 200 · sections 140 · notes 32 · boilerplate patterns 3
  chunks: caption <≈17>, chart <≈52>, footnote 32, section_summary <≈44>, table <≈130>, text <≈315> (total <≈590>)
  unresolved footnote markers: p130:2
```

Run: `cd ingest && uv run python validate.py --doc gep`
Expected: 16 `✓ gep:` lines (6 generic and 10 GEP), exit 0.

Run a second time: `cd ingest && uv run python ingest.py --doc gep`
Expected: the same report within about a minute. Embeddings and summaries come from `ingest/.cache/gep/`.

- [ ] **Step 6: Commit**

```bash
git add ingest/validate.py ingest/tests/test_validate.py
git commit -m "Add GEP validation checks"
```

---

## Sub-project acceptance (spec §10, row 4)

- [ ] `cd ingest && uv run pytest -q` → 180 passed, 2 deselected; `uv run pytest -m live` → 2 passed
- [ ] `uv run python ingest.py --doc gep` → ~140 sections; 6 regions on pages 81–83 … 99–102; ~44 section summaries; a rerun makes no API calls
- [ ] `uv run python validate.py` → gpo, gep and imac generic checks plus the GPO and GEP checks all ✓
- [ ] The brief's GEP questions have their material in place: South Asia outlook text, Table 2.9 and Figure 2.7 under `Ch. 2 Regional Outlooks › South Asia`; a Chapter 3 summary chunk; Executive Summary text
- [ ] `git log --oneline main..HEAD` shows the spec update, the plan and 6 short task commits, with no trailers

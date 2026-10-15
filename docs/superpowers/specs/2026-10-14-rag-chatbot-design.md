# Document-Aware RAG Chatbot — Design Spec

- **Date:** 2026-10-14
- **Status:** Draft — awaiting review
- **Brief:** `docs/RAG-Hands-On-Use-Cases.docx`

## 1. Goal

Build one question-answering chatbot over three PDFs whose structures differ on purpose, and show that RAG must be designed per document shape rather than applied identically everywhere.

| # | Document | File | Pages | Shape | Core challenge |
|---|----------|------|-------|-------|----------------|
| 1 | GPO 2018 Annual Report | `docs/GPO-Anual-report.pdf` | 82 | Narrative text | Clean baseline: chunking, embeddings, grounded answers |
| 2 | World Bank Global Economic Prospects, June 2026 | `docs/GEP-Jun-2026.pdf` | 200 | Chapters → sections → sub-sections, boxes, statistical annex | Hierarchical chunking, section-aware retrieval, no cross-region mix-ups |
| 3 | Apple iMac Product Environmental Report (Oct 2024) | `docs/iMac_PER_Oct2024.pdf` | 12 | Charts, tables, callouts, endnotes | Facts outside paragraphs: tables, charts, footnotes |

### Success criteria (pass bar)

1. All 9 brief questions (3 per document, §8.1) answered correctly with page/section citations.
2. Every trap (out-of-document) question is refused instead of answered.
3. The region-isolation check passes (no facts mixed across GEP regions).
4. No answer contains a number that is absent from its cited sources (verifier, §6.8).

### Non-goals (this version)

- Uploading or ingesting new documents from the UI (ingestion is a CLI over the 3 fixed PDFs).
- Authentication, multi-user persistence of chat history, production deployment.
- Vision processing for GEP figures (profile switch exists, off by default).

## 2. Findings from document inspection

These facts drove the design:

- **GPO** — text layer extracts cleanly; photo captions (`(Above) …`) are interleaved with body text.
- **GEP** — PDF bookmarks give the top-level tree (Executive Summary p19, Ch.1 p23, Ch.2 p69, six regions p81–99 (the South Asia bookmark is one page early: p95, the region starts p96), Ch.3 p107, Ch.4 p143, Statistical Annex p185, Boxes 1.1/1.2/2.1/3.1). The printed Contents pages (PDF p9–17) list sub-sections with printed page numbers; printed page 1 = PDF page 23. Running headers repeat on every page (`CHAPTER 1`, `GLOBAL ECONOMIC PROSPECTS | JUNE 2026`).
- **iMac** —
  - p2 carbon chart: only labels and totals extract (`445 kg`, `–99 kg`, `346 kg`); segment values exist only as bar heights.
  - p10 carbon table extracts as loose layout text: total 346 kg CO2e; production 50%, transportation 5%, product use 45%, end-of-life <1%.
  - p12: the power-consumption table (Off/Sleep/Idle by 100V/115V/230V) sits **inside endnote 18**.
  - Body claims carry superscript endnote markers (`58 percent less energy … ENERGY STAR.18`); endnotes live on p11–12.
  - Icon callouts (`100% recycled aluminum in the stand`) are short, dense facts.
- **Design rule derived:** the same fact often appears in both a chart and a table — index both, cite whichever matched.

## 3. Architecture

```
            ┌──────────────── Python (offline) ────────────────┐
 docs/*.pdf │ parse → strip boilerplate → profile chunker →     │
            │ enrich (footnotes, section path, vision, summary) │──► Postgres + pgvector
            │ → embed (Gemini) → store                          │        ▲
            └───────────────────────────────────────────────────┘        │ SQL
                                                                          │
            ┌──────────────── Next.js (TypeScript) ─────────────┐        │
 Browser ──►│ /api/chat: condense → route → hybrid search →     │────────┘
            │ rerank → assemble → refuse? → generate (stream) → │──► Gemini
            │ verify                                            │
            └───────────────────────────────────────────────────┘
```

- **Hybrid split:** Python owns ingestion (best PDF tooling); Next.js owns serving (retrieval + generation + UI).
- **Contract:** `db/schema.sql` is the only interface between the two halves. Both must use the same embedding model and dimension; this is stored in `documents` and checked by the server at startup.

### 3.1 Tech stack

| Concern | Choice |
|---------|--------|
| Ingestion | Python 3.10, PyMuPDF, `google-genai`, `psycopg`, `pydantic`, `tiktoken` (`cl100k_base`) as the chunk-size proxy, pytest |
| Storage | Postgres 16 + pgvector (Docker Compose) |
| Embeddings | `gemini-embedding-001`, output dimensionality **768**, task types `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` |
| LLM | Gemini Flash-class model for routing, reranking, generation, vision, section summaries |
| Serving | Next.js (App Router, TypeScript), `@google/genai` (one client for query embeddings, helper calls and streamed answers), `pg`, `zod`, vitest |

Model IDs are configured in `.env` (`GEMINI_CHAT_MODEL`, `GEMINI_EMBED_MODEL`, `EMBED_DIM`), never hard-coded. Current IDs are confirmed against Gemini docs at implementation time.

### 3.2 Repository layout

```
rag-chatboat/
├── docs/                         source PDFs, brief, specs, plans
├── docker-compose.yml            postgres:16 + pgvector
├── .env.example                  GEMINI_API_KEY, DATABASE_URL, model IDs, EMBED_DIM
├── db/schema.sql
├── ingest/
│   ├── ingest.py                 CLI: python ingest.py [--doc gpo|gep|imac] [--reset]
│   ├── profiles.py               per-document profile config
│   ├── parse/  pdf_text.py · boilerplate.py · tables.py · outline.py · footnotes.py · vision.py
│   ├── chunk/  narrative.py · hierarchical.py · visual.py · common.py
│   ├── enrich/ summaries.py
│   ├── embed.py · store.py · gemini.py · cache.py
│   ├── validate.py               post-ingest assertions
│   └── tests/
├── web/
│   ├── app/page.tsx              chat UI
│   ├── app/api/chat/route.ts
│   ├── app/api/pdf/[slug]/route.ts   serves PDFs for "open page" links
│   └── lib/rag/  condense.ts · router.ts · retrieve.ts · rerank.ts · context.ts · prompt.ts · verify.ts · gemini.ts · db.ts · numbers.ts
├── web/lib/eval/ · web/scripts/eval.ts   evaluation runner (`npm run eval`, run with tsx)
└── eval/
    ├── questions.yaml
    └── results/                  run outputs (git-ignored)
```

## 4. Data model (`db/schema.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
  id           serial PRIMARY KEY,
  slug         text UNIQUE NOT NULL,        -- gpo | gep | imac
  title        text NOT NULL,
  profile      text NOT NULL,               -- narrative | hierarchical | visual
  pages        int  NOT NULL,
  embed_model  text NOT NULL,
  embed_dim    int  NOT NULL,
  ingested_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sections (
  id          serial PRIMARY KEY,
  doc_id      int NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  parent_id   int REFERENCES sections(id) ON DELETE CASCADE,
  title       text NOT NULL,
  level       int  NOT NULL,                -- 0 = document root
  path        text NOT NULL,                -- "Ch. 2 Regional Outlooks › South Asia › Outlook"
  kind        text NOT NULL DEFAULT 'section',  -- section | box | region | annex | front
  page_start  int NOT NULL,
  page_end    int NOT NULL,
  content     text                          -- full section text (parent context)
);

CREATE TABLE chunks (
  id          serial PRIMARY KEY,
  doc_id      int NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_id  int REFERENCES sections(id) ON DELETE CASCADE,
  seq         int NOT NULL,                 -- order within section (neighbour expansion)
  kind        text NOT NULL,                -- text | table | chart | callout | caption | footnote | section_summary
  content     text NOT NULL,                -- what the LLM sees
  embed_text  text NOT NULL,                -- context header + content; embedded and keyword-indexed
  page_start  int NOT NULL,
  page_end    int NOT NULL,
  footnotes   jsonb NOT NULL DEFAULT '[]',  -- [{n, text}]
  metadata    jsonb NOT NULL DEFAULT '{}',  -- e.g. {estimated: true, caption: "..."}
  embedding   vector(768) NOT NULL,
  tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', embed_text)) STORED
);

CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin        ON chunks USING gin (tsv);
CREATE INDEX chunks_doc_section    ON chunks (doc_id, section_id, seq);
```

`embed_text` header format: `"<doc title> › <section path> | p.<start>[-<end>] | <kind>\n\n<content>"`.

## 5. Ingestion (Python)

### 5.1 Shared pipeline (all profiles)

1. **Parse** — PyMuPDF `page.get_text("dict")` → lines/spans with font size, flags (bold, superscript), bbox, page number. All text is NFKC-normalized (GEP uses ligature glyphs such as `ﬀ`/`ﬂ`, which would break keyword search). GEP also maps its "Th" ligature glyph to a bare `T` (`Te`, `Tird`), which NFKC cannot fix: a `T` glyph wider than 0.85× the font size is emitted as `Th` (plain `T` is 0.56–0.66×, the ligature 1.0–1.1×). Wrapped lines are joined by dropping a line-end hyphen only when the joined word appears elsewhere in the document; otherwise the hyphen is kept (`middle-income`).
2. **Strip boilerplate** — lines in the top/bottom 8% of the page are normalized (lower-case, whitespace removed, digit runs → `#`) and removed when they repeat on ≥ 15% of pages (minimum 3). 15%, not 30%: GPO alternates left/right running headers and letter-spaces them (`U . S . G O V E R N M E N T …`), so each variant appears on only ~17% of pages. Profiles can list pages to skip (GEP copyright, contents, abbreviations).
3. **Tables** — PyMuPDF `find_tables()` was tested and rejected: on these PDFs it returned only the header row of the iMac carbon table, truncated the power table, merged GEP Table 1.1 into one cell, found no Statistical Annex tables, and flagged a GPO photo caption as a table. Instead the core offers **row reconstruction over a region**: spans inside a bbox are grouped into rows by vertical centre and split into cells at horizontal gaps > 6pt, then rendered as markdown. Profiles decide where table regions are (GEP: caption `TABLE x.y` down to its `Source:`/`Note:` lines; GPO: financial-statement pages; iMac: vision). Each table becomes its own `kind=table` chunk, prefixed with its caption and section path. Never split mid-row; tables over the chunk limit split by row groups with the header row repeated.
4. **Footnotes** — a note starts on a line whose first span is a 1–3 digit number in a font < 0.8× the following text, with that text smaller than body size (GEP: 4.8pt number + 8pt text at the page bottom; iMac: 4.7pt number + 7pt text on endnote pages 11–12, mostly *without* the superscript flag). Following lines of the same size that sit directly below continue the note. Markers are body spans with the superscript flag whose text is digits (`18`, `12,13`) in a font < 0.85× the line's main size; they are removed from the text and recorded on the line as `(page, n, offset)`; the offset follows the text through paragraph joins and sentence splits, so a ref belongs only to the chunk piece that contains the marker and is resolved against its own page (chunks often span pages). A marker resolves to the note with that number on the same page, else to that number on the document's endnote pages (profile-configured: iMac p11–12); otherwise it is reported as unresolved. No nearest-page fallback: GEP restarts numbering per chapter, and most of its 148 superscript digits are table/figure note markers (only 32 page footnotes exist), which a fallback would attach to the wrong note. Each chunk records `footnotes: [{n, text}]` for markers it contains. Each note is also indexed alone as `kind=footnote`. Unresolved markers are reported.
5. **Chunk** — profile-specific (5.2–5.4). Token counts use a single shared tokenizer helper.
6. **Embed** — batched Gemini embeddings of `embed_text`; cached on disk by content hash.
7. **Store** — per document, in one transaction: delete the document's rows, insert document → sections → chunks.
8. **Report** — printed summary: chunks per kind, sections found, headings not located, unresolved footnote markers, vision/text number mismatches, fallbacks used.

### 5.2 GPO — `narrative` profile

Probing showed GPO marks headings by **font face and colour, not size** (narrative body is Garamond Light 9.8pt; the computed body size is 12pt only because of the financial half), so the rules are style-based:

- **Skipped pages:** 1, 29–33 (covers, a blank page, the back cover and the statements' contents page).
- **Extra boilerplate band:** lines repeated on ≥ 3 pages within 12% of the top/bottom edge are also removed — the financial pages' footer ("…Financial Year In Review – FY 2018") and page numbers sit 9–11% from the bottom, outside the shared 8% band.
- **Narrative pages (≤ 30):** level 1 = HelveticaNeue Medium 10pt grey `#646e74` ("GPO and Congress"); level 2 = HelveticaNeue Medium 9.5pt blue `#507cbf`, standalone or **run-in** (the heading starts a body line; the rest of the line stays body text). The same face in dark grey `#404041` is inline emphasis/URLs and in teal `#4e89a8` chart titles — neither is a heading.
- **Financial pages (≥ 34):** single-column, so lines are read top to bottom (PDF order puts some note tables after the next heading). Level 1 = lines ≥ 20pt (divider pages "SECTION I" + "MANAGEMENT DISCUSSION AND ANALYSIS (UNAUDITED)"); level 2 = Arial Bold or Times New Roman Bold (the OIG memo's Cambria/Calibri Bold labels are not headings), 11.5–14.5pt, < 90 chars, not numeric, not ending in ":", **alone on its row** (rules out bold table labels and memo fields), and not a title-block line repeated on ≥ 3 pages ("U.S. Government Publishing Office").
- **Titles:** level-1 lines on one page, and vertically adjacent level-2 lines, merge into one title. A page-top title that PDF order places after body text is moved first. Where the PDF has no heading, the profile injects one: p2 "Letter of Transmittal", p7 "About GPO"; p28 "Organizational Chart"; the two infographic pages 14–15 share one stand-alone section "GPO By the Numbers FY 2018", after which the interrupted section resumes.
- **Sections:** root → level 1 → level 2, path `"<L1> › <L2>"`. Text chunks ~500 tokens, ~75 overlap, packed per section — never across a heading.
- **Captions:** HelveticaNeue Roman 8.2pt lines on narrative pages → `kind=caption` chunks, kept out of text chunks.
- **Drop caps:** a one-letter line ≥ 3× body size rejoins the line it starts ("T" + "he U.S. Government…").
- **Tables:** pages 14, 15, 58, 59, 60 → row reconstruction over the page (heading, margin and repeated title lines excluded), lone `$` cells merged with their value, short all-number rows (`2018 | 2017`) right-aligned over the value columns, header = rows through the `2018 | 2017` row, split at ≤ 450 tokens with the header repeated.
- No routing hints, no summaries — this is the control case.

### 5.3 GEP — `hierarchical` profile

Probing showed the PDF's typography is a cleaner source of structure than its contents pages, and that **bookmarks are unreliable below chapter level** (the "South Asia" bookmark points to p95, but the region starts on p96). The tree therefore comes from bookmarks for chapters only and from heading styles for everything else:

- **Skipped pages:** 1–14 (cover, copyright, contents) and 192–200 (publication lists, back cover).
- **Reading order:** each page's lines are ordered left column then right column, top to bottom (lines wider than 55% of the page count as left). PDF stream order often starts with the right column or a heading block, which misfiled ~11% of lines before this rule.
- **Level 1:** chapter bookmarks (`Ch. 1 Global Outlook` … `Ch. 4 …`, `Statistical Annex`) at their start pages, and front-matter titles in HelveticaLTStd-Cond 26pt (Acknowledgments, Foreword, Executive Summary, Abbreviations; `kind=front`).
- **Level 2:** HelveticaLTStd-Bold 16pt (or 14pt — Ch. 2's "References") in any colour: chapter sections ("Summary", "Global context"), the six regions (`kind=region`), annexes `ANNEX x.y …` (`kind=annex`). Wrapped heading lines merge ("Latin America and" + "the Caribbean"); a "… (continued)" heading reopens the current section.
- **Boxes:** a `BOX x.y …` title (HelveticaLTStd-Bold 10pt) opens a `kind=box` section under the chapter; later pages repeat the title as a running header. Box text is Garamond **10.6pt** while chapter text is **11pt**, so on shared pages each body line is routed to the box or the chapter by size. Box figures/tables (`FIGURE B1.1.1`) belong to the box.
- **Levels 3–4:** HelveticaLTStd-Roman 11pt sub-headings in any blue (`#4475a1`, and `#215e99`/`#4f81bd`/`#2e73b5` in some boxes and Ch. 3) ("Recent developments", "Outlook", "Risks", box sub-heads — the next body line's size decides box vs chapter) and AGaramondPro-BoldItalic 11pt blue minor headings ("Downside risks"). Wrapped lines merge.
- **Excluded:** "References" sections keep their place in the tree but produce no chunks (bibliographies would swamp keyword search with author names).
- **Chunks:** text ~350 tokens, ~50 overlap, per leaf section. `sections.content` holds each section's own text.
- **Objects:** FIGURE/TABLE/BOX titles are HelveticaLTStd-Bold 10pt (TABLE 4.1 is 11pt); numbers may carry a `B` (box) or `A` (annex) prefix.
- **Figures:** each `FIGURE x.y` title owns the Helvetica lines below it on its page (until the next title) → one `kind=chart` chunk: title, the italic takeaway paragraph (HelveticaLTStd-Oblique 8pt), panel titles ("A. …", Bold 7pt), and Sources/Note lines (Roman 6pt). Chart tick labels are dropped. No vision.
- **Tables:** each `TABLE x.y` title's cell lines → row reconstruction, `$` merge, header rows aligned (year cells such as `2026f`, `24Q4`, `26Q1e`, `2025/26e` count as numeric), repeated year columns labelled "(change vs January 2026 projections)", split at ≤ 400 tokens with the header repeated and the title on every piece; the table's Sources/Note lines become a separate `kind=caption` chunk. Statistical Annex pages 187–190 are whole-page tables titled by their heading. Table cells never carry footnote refs (their superscripts are table notes).
- **Section summaries:** every level-1/2 section whose text (including descendants) is ≥ 300 tokens gets a 3–5 sentence Gemini summary (≈ 44 for this PDF), stored as a `kind=section_summary` chunk "Summary of <path>: …". Prompts are truncated to 6,000 tokens; results are cached by model + prompt version + prompt; generation calls are rate-limited (`GEMINI_CHAT_REQUESTS_PER_MINUTE`, default 10).

### 5.4 iMac — `visual` profile

- **Vision:** every page is rendered at 150 dpi and sent to Gemini (`GEMINI_VISION_MODEL`, default = the summary model; Flash-Lite in practice because of free-tier quotas) with a JSON schema: `{blocks: [{kind: text|table|chart|callout, title, markdown, values: [{label, value, unit, estimated}], footnote_refs: [n]}]}`. Answers are cached by model + prompt version + page-image hash, so reruns are free.
- **Prompt rules:** transcribe exactly, in reading order; tables as markdown; chart segments without printed values are estimated from bar proportions anchored on printed totals and flagged `estimated: true`; superscript digits are footnote refs, not text.
- **Cleanup** (the model does not always follow the rules): Unicode superscript digits and listed refs glued to a word ("emissions7", "STAR.^18") are removed from the text and recorded as refs; text is NFKC-normalized (`CO₂e` → `CO2e`); wrapped table rows are rejoined; a heading repeated in the markdown is dropped; `**bold**` markup is removed.
- **Vector charts are measured, not estimated:** where a page draws stacked bars as filled rectangles with a printed total above each bar (iMac p2), each segment's height is scaled to that total (bars with a negative total, a waterfall's reductions, use the median scale). This replaces the vision chart blocks: on p2 the vision estimates said product use ≈ 56% while the measured shapes give 156 kg = **45%**, matching the printed p10 table. Measured values are stored as `Values (measured from the chart's shapes): label: value unit (share%)` with `metadata.measured = true`.
- **Cross-check:** a chart value marked as printed that does not appear in the page's text layer is re-marked approximate (warning); the model's own estimates are dropped (warning) when they miss the printed total by more than 3%; table/callout numbers missing from the text layer are reported as warnings.
- **Footnote refs from the text layer:** markers the model drops entirely (iMac p1, p5) are recovered from the PDF's text layer and attached to the block holding the words just before the marker. Marker stripping never touches a digit after a decimal point ("91.1%").
- **Sections:** one per page title (the 24pt lines, e.g. "Progress toward carbon neutral", "Carbon Footprint"); consecutive pages with the same title (Endnotes p11–12) share a section.
- **Chunks:** text blocks are packed per section (~400 tokens, 50 overlap); tables, charts and callouts are their own chunks. Chart content ends with `Values: label: value unit (approximate, read from chart); …` and estimated charts carry `metadata.estimated = true`.
- **Endnote pages (11–12):** only table blocks are kept — note text is already indexed as footnote chunks. A table there is linked to the note whose text names at least two of its row labels (note 18's power-consumption table).
- **Fallback:** if vision fails for a page, that page's text layer becomes ordinary paragraphs and the report carries a warning.

### 5.5 Caching and repeatability

- `ingest/.cache/` stores vision JSON (key: page image hash + prompt version), summaries (key: section text hash + prompt version), embeddings (key: model + dim + text hash).
- Re-runs are cheap and deterministic. `--doc` limits to one document; `--reset` drops the cache entries for that document.

## 6. Retrieval and answer generation (Next.js `/api/chat`)

Request (zod-validated): `{ question: string (≤ 1000 chars), scope: "gpo"|"gep"|"imac"|"all", history: {role, content}[] (last 6), options?: {vectorOnly?, noRerank?, noRouter?, noFootnotes?} }`. `options` are accepted only when `NODE_ENV !== "production"` and arrive with the evaluation sub-project (§8.3).

**Models:** the final answer uses `GEMINI_CHAT_MODEL` (Flash). Condensing and reranking use `GEMINI_UTILITY_MODEL` (default: the summary model, Flash-Lite), because the free tier allows Flash only ~20 generations a day.

1. **Condense** (`condense.ts`) — only if `history` is non-empty: Gemini rewrites the question as standalone.
2. **Route** (`router.ts`)
   - Deterministic regexes first: `chapter \d`, `box \d\.\d`, `executive summary`, `statistical annex`, region names + aliases (EAP, ECA, LAC, MNA/MENAAP, SAR, SSA). An explicit match → **hard filter** on matching `section_id`s (including descendants).
   - In `all` scope, document keywords pick the documents (GPO: "GPO", "govinfo", "depository"…; iMac: "iMac", "ENERGY STAR"…; GEP: "World Bank", "EMDE", region names…), falling back to all three; a GEP section match restricts the search to GEP.
   - No LLM routing call: the reranker already judges relevance, and an LLM router would spend scarce free-tier generations. Out-of-scope questions are refused by the relevance gate (step 6).
3. **Hybrid search** (`retrieve.ts`, single SQL statement)
   - Vector top 40 by cosine distance (HNSW) ∪ keyword top 40 by `ts_rank_cd(tsv, websearch_to_tsquery('english', q))`.
   - Keyword query = the question's words joined with OR (`websearch_to_tsquery`), so long questions still match.
   - Fused with reciprocal rank fusion, k = 60. Section-summary chunks compete in the same search (they win broad questions).
4. **Rerank** (`rerank.ts`) — one Gemini call scores the top 20 candidates 0–3 ("does this passage answer the question?"); keep the top 8 with score ≥ 2.
5. **Assemble context** (`context.ts`)
   - GEP child chunks: add ±1 neighbour (`seq`) in the same section; merge adjacent.
   - Label each block: `[S1] <doc title> › <section path> | p.<page> | <kind>`, then content, then `Footnotes:` lines (unless `noFootnotes`).
   - Budget ~6k tokens; drop lowest-scored blocks first.
6. **Refusal gate** — if no candidate scores ≥ 2, or `intent = out_of_scope`: return the fixed message `I couldn't find this in <document>.` plus the top-3 closest section paths. **No generation call.**
7. **Generate** (`prompt.ts`, temperature 0.1, streamed). System rules:
   - Answer only from the sources; cite every claim as `[S#]`.
   - Copy numbers verbatim with units and time qualifiers (`FY 2018`, `2026e`).
   - Do not combine facts from different regions/chapters unless the question asks for a comparison; for a question about region X, use only sources from section X.
   - Present values marked `approximate, read from chart` as approximate; mention footnote caveats that limit a claim.
   - If the sources answer only part of the question, say what is missing; never fill gaps.
8. **Verify** (`verify.ts`, deterministic, after stream end)
   - Every `[S#]` must reference a provided source.
   - Every number in the answer must appear in at least one cited source after normalization (`numbers.ts`: thousands separators, `percent`↔`%`, `$` and `billion/million`, decimals, `<1%`).
   - Failures are returned as `warnings[]`; the UI shows an "unverified figure" badge.

**Response:** `application/x-ndjson`, one JSON event per line: `{type: "sources", sources: [{id, doc, sectionPath, pageStart, pageEnd, kind, snippet}]}`, then `{type: "text", delta}` events, then `{type: "done", warnings, debug: {question, route, candidates: [{chunkId, vecRank, kwRank, rrf, rerank}]}}`; failures send `{type: "error", message}`. Invalid requests get HTTP 400; an embedding/schema mismatch or unreachable DB gets HTTP 503 before streaming.

### 6.1 UI (`app/page.tsx`)

- Document selector (GPO / GEP / iMac / All documents), chat thread, streaming answer.
- Citation chips `[S#]` open a sources drawer: chunk text, section path, page, kind, "Open page" link (`/api/pdf/<slug>#page=N`).
- "Show retrieval" toggle renders the debug block (router output, ranks, rerank scores).
- Warning badge on answers with verifier failures.

## 7. Error handling

| Where | Failure | Behaviour |
|-------|---------|-----------|
| Ingest | Gemini call error / 429 | Retry with exponential backoff, 3 attempts |
| Ingest | Vision fails for a page | Fall back to text-layer chunks; flagged in report |
| Ingest | Crash mid-document | Transaction rolls back; previous index for that doc intact |
| Serve | Embedding model/dim mismatch vs `documents` | Fail at startup with a clear message |
| Serve | Router fails | Continue without filters/boosts |
| Serve | Rerank fails | Use RRF order, top 8 |
| Serve | Generation fails | Error message in chat; sources still shown |
| Serve | DB unreachable | HTTP 503 with message |
| Serve | Invalid request | HTTP 400 (zod) |

`GEMINI_API_KEY` is read only server-side; never exposed to the client bundle.

## 8. Evaluation

### 8.1 Question set (`eval/questions.yaml`)

Each entry: `{id, question, scope, type: brief|coverage|trap|isolation, must_contain[], must_not_contain?[], expected_pages[], expected_section?, rubric?[]}`. Pages are PDF page numbers; `"107-138"` is an inclusive range. `must_contain` and `rubric` values are taken from the actual source pages during implementation — never written from memory.

- **Brief (9)**
  - GPO: depository libraries visited in FY 2018 · system that replaced FDsys and when fully deployed · printing procured for agencies in FY 2018.
  - GEP: South Asia growth outlook · Chapter 3 on sovereign debt in developing economies · Executive Summary main risks.
  - iMac: energy use vs ENERGY STAR requirement · total carbon footprint and use vs manufacturing share · stand material and recycled percentage.
- **Coverage (4)** — iMac sleep power at 230V (`0.59W`, table in endnote 18) · iMac carbon split (`50%`, `45%`, p10) · one GEP Statistical Annex forecast row (Bhutan 2026f, `7.1`, p189) and one GEP Box question (BOX 1.1: AI growth in the 2030s, `1970s`, pp30–35).
- **Trap (4)** — iMac battery life · GPO budget in FY 2022 · GEP growth outlook for Antarctica · one off-topic question (e.g. "Who won the 2022 FIFA World Cup?").
- **Isolation (1)** — East Asia and Pacific growth outlook: all sources must be EAP (or Statistical Annex rows); `must_not_contain` holds South Asia-only figures (`6.3%`, `6.6%`).

### 8.2 Scorecard (`npm run eval`)

Per question:
- **Retrieval hit** — a final source sits in `expected_section` (or below it) or covers an expected page.
- **Fact match** — every `must_contain` appears in the answer after normalization (case, dashes, thousands separators, `percent` → `%`).
- **Citation precision** — the share of cited sources that are relevant (as for retrieval hit).
- **Verifier clean** — no verifier warnings (the "reranking failed" notice is not a verifier warning).
- **Trap refused** — the answer starts with "I couldn't find this" (the gate's message or the prompt's refusal rule). No numbers check: the gate's "Closest sections" list carries chapter numbers.
- **Isolated** (isolation only) — every source in `expected_section` or the Statistical Annex, and no `must_not_contain` figure in the answer.
- **Pass** — brief/coverage: hit, fact match and at least one relevant citation; isolation: also isolated; trap: refused. The pass bar (§1) is computed from these, plus "verifier clean" over all answered questions.

The two broad GEP questions additionally get a judge (the utility model) scoring rubric coverage and unsupported claims; it is reported, not gated. Results are written to `eval/results/<timestamp>.json` and printed as a table; the command exits 1 when the pass bar is not met. `--only <id,id>` runs a subset (no pass bar).

### 8.3 Ablations

`npm run eval -- --ablate` runs the set with `vectorOnly`, `noRerank`, `noRouter`, `noFootnotes` and prints a side-by-side table (questions passed per document) against the full pipeline, showing what each technique contributes per document shape.
- Ablation runs answer with the **utility model** (Flash-Lite), against a utility-model baseline `full (lite)`: Flash allows ~20 answers a day, and five runs of 18 questions need ~70. The main run stays on Flash.
- `--config <name>` runs one ablation; query embeddings are shared across configs.
- `vectorOnly` passes a NULL keyword query (no keyword rows); `noRerank` keeps the top 8 by RRF with no relevance gate; `noRouter` searches the scope's documents without section filters; `noFootnotes` leaves `Footnotes:` lines out of the context.

## 9. Testing

- **Python (pytest, offline)** — footnote linking (iMac p7 marker 18 → note 18 text) · boilerplate stripping · GEP line roles, reading order and section tree (six regions on their real pages, boxes, wrapped headings) · chunker invariants (no chunk crosses a section; size bounds; tables never split mid-row) · vision cross-check using fixture JSON · number normalization.
- **Post-ingest (`ingest/validate.py`)** — GEP has 6 region sections with page ranges inside p81–106, the South Asia outlook text and Table 2.9 under South Asia, Executive Summary text, a Chapter 3 summary, BOX 1.1, ≥ 40 chart / ≥ 100 table / ≥ 30 summary chunks, and no reference-list text · iMac: table chunks with `0.59W` and `45%`, a measured chart chunk, the "58 percent less energy" text carrying footnote 18, "recycled aluminum in the stand", 19 footnote, ≥ 3 callout and ≥ 3 chart chunks · GPO: the three brief facts sit in the expected chunks (166 libraries; govinfo deployment in the Acting Deputy Director's message; "$375 million" in text and "$375.7 million" in the By-the-Numbers table), a `› govinfo` section exists, ≥ 15 caption and ≥ 4 table chunks · every chunk has a non-empty embedding of `EMBED_DIM`.
- **TypeScript (vitest)** — router regexes · RRF fusion · number normalization and verifier · prompt/context assembly · one SQL integration test against the Docker DB.
- **End-to-end** — the eval scorecard (§8) is the acceptance test.

## 10. Delivery breakdown

The work is split into sub-projects. Each gets its own implementation plan in `docs/superpowers/plans/`, is built and verified before the next starts, and is broken into small tasks with one short commit each.

| # | Sub-project | Plan file | Done when |
|---|-------------|-----------|-----------|
| 1 | Foundation — Docker Postgres+pgvector, schema, `.env.example`, Python and Next.js skeletons, Gemini client wrappers | `01-foundation.md` | `docker compose up` works, schema applies, both skeletons run, a Gemini embed call succeeds |
| 2 | Shared ingestion core — parsing, boilerplate, tables, footnotes, chunk helpers, embed, store, cache, report, CLI | `02-ingest-core.md` | Unit tests pass; a dummy profile ingests a PDF end-to-end |
| 3 | GPO narrative profile | `03-gpo-narrative.md` | GPO ingested; validate.py GPO checks pass |
| 4 | GEP hierarchical profile — section tree from bookmarks + typography, boxes, figures, tables, summaries | `04-gep-hierarchical.md` | GEP ingested; section tree and region checks pass |
| 5 | iMac visual profile — vision transcription, cross-check, endnotes | `05-imac-visual.md` | iMac ingested; table/footnote checks pass |
| 6 | Retrieval API — condense, router, hybrid SQL, rerank, context, refusal, generation, verifier | `06-retrieval-api.md` | vitest passes; `/api/chat` answers via curl with sources |
| 7 | Chat UI — selector, streaming, citations drawer, PDF links, debug toggle, warnings | `07-chat-ui.md` | Manual walkthrough of all 3 documents in the browser |
| 8 | Evaluation — question set, scorecard, judge, ablations | `08-evaluation.md` | Pass bar in §1 met; ablation table produced |

Sub-projects 3–5 depend on 2; 6 depends on at least one ingested document; 7 depends on 6; 8 depends on 6 (and fills `must_contain` from source pages early so it can guide 3–6).

**Commit convention:** short, imperative, plain English, ≤ 50 characters, one logical change per commit (e.g. `Add pgvector schema`, `Link iMac endnotes to chunks`).

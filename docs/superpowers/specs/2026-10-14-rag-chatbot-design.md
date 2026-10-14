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
- **GEP** — PDF bookmarks give the top-level tree (Executive Summary p19, Ch.1 p23, Ch.2 p69, six regions p81–99, Ch.3 p107, Ch.4 p143, Statistical Annex p185, Boxes 1.1/1.2/2.1/3.1). The printed Contents pages (PDF p9–17) list sub-sections with printed page numbers; printed page 1 = PDF page 23. Running headers repeat on every page (`CHAPTER 1`, `GLOBAL ECONOMIC PROSPECTS | JUNE 2026`).
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
| Serving | Next.js (App Router, TypeScript), Vercel AI SDK + `@ai-sdk/google`, `pg`, `zod`, vitest |

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
└── eval/
    ├── questions.yaml
    └── run_eval.ts
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

1. **Parse** — PyMuPDF `page.get_text("dict")` → blocks/spans with font size, flags (bold, superscript), bbox, page number.
2. **Strip boilerplate** — normalized lines in the top/bottom 8% of the page that repeat on >30% of pages are removed (running headers, footers, page numbers). Profiles can list pages to skip (GEP copyright, contents, abbreviations).
3. **Tables** — `page.find_tables()` → markdown. Each table becomes its own `kind=table` chunk, prefixed with its caption (nearest `TABLE x.y …` line above) and section path. Never split mid-row; tables over the chunk limit split by row groups with the header row repeated.
4. **Footnotes** — body spans with the superscript flag (or smaller font on a raised baseline, digits only) are markers. Note text is found at the bottom of the same page (small font, line starting with the number) or in an endnotes section (profile-configured pages). Each chunk records `footnotes: [{n, text}]` for markers it contains. Each note is also indexed alone as `kind=footnote`. Unresolved markers are reported.
5. **Chunk** — profile-specific (5.2–5.4). Token counts use a single shared tokenizer helper.
6. **Embed** — batched Gemini embeddings of `embed_text`; cached on disk by content hash.
7. **Store** — per document, in one transaction: delete the document's rows, insert document → sections → chunks.
8. **Report** — printed summary: chunks per kind, sections found, headings not located, unresolved footnote markers, vision/text number mismatches, fallbacks used.

### 5.2 GPO — `narrative` profile

- Headings: font size ≥ body size + 2pt or bold standalone line → starts a new flat section.
- Chunks: ~500 tokens target, ~75 overlap, split at paragraph then sentence boundaries; never cross a heading.
- Photo captions matching `^\((Above|Below|Left|Right|Top|Bottom)[^)]*\)` are split out as `kind=caption`.
- No routing hints, no summaries — this is the control case.

### 5.3 GEP — `hierarchical` profile

**Section tree**
1. Bookmarks (`doc.get_toc()`) → top level: front matter, Executive Summary, Ch.1–4, Boxes, six regions, Statistical Annex.
2. Parse printed Contents pages (PDF p9–17): lines `Title ……… <page>` with indentation level → sub-sections.
3. Map printed → PDF pages using the offset learned from bookmarks (printed 1 = PDF 23); roman-numeral front matter mapped from bookmarks directly.
4. Locate each heading on its PDF page by fuzzy match (normalized, ratio ≥ 0.85) to get its y-position; sections can start mid-page.
5. Fill gaps with font-size heading detection inside leaf sections (e.g. `Recent developments`, `Outlook`, `Risks`).
6. Boxes are their own sub-trees (`kind=box`); text inside a box's page/y-range belongs to the box, not the surrounding chapter.

**Chunks**
- Child chunks ~350 tokens, ~50 overlap, within one leaf section only.
- `sections.content` holds each section's full text (parent context).
- One `kind=section_summary` chunk per section with ≥ 300 tokens of text: Gemini-generated 3–5 sentence summary (~120 calls, cached).
- Figures: caption + `Sources:` / `Note:` text → `kind=chart` chunk. No vision (profile switch `vision_pages: []`).
- Tables (including Statistical Annex country forecast tables) via the shared table step.

### 5.4 iMac — `visual` profile

- Every page rendered at ~150 dpi PNG → Gemini vision with a JSON response schema:
  ```json
  {"blocks": [{"kind": "text|table|chart|callout",
               "title": "string",
               "markdown": "string",
               "values": [{"label": "string", "value": "number|string", "unit": "string", "estimated": "boolean"}],
               "footnote_refs": ["int"]}]}
  ```
- Prompt rules: transcribe exactly; tables as markdown; chart segments without printed values are estimated from bar proportions anchored on printed totals and flagged `estimated: true`; superscript digits are footnote refs, not part of numbers.
- **Cross-check:** each non-estimated numeric value must appear in the page's text layer (after number normalization). On mismatch, text-layer value wins and a warning is logged. Estimated values are stored with the suffix `(approximate, read from chart)` and `metadata.estimated = true`.
- Each block becomes one chunk of its `kind`; callouts stay small.
- Endnotes (p11–12) parsed via the shared footnote step; the power-consumption table inside note 18 becomes a `kind=table` chunk with `footnotes: [{n: 18, …}]`.
- Fallback: if vision fails after retries, that page uses text-layer chunks and the report flags it.

### 5.5 Caching and repeatability

- `ingest/.cache/` stores vision JSON (key: page image hash + prompt version), summaries (key: section text hash + prompt version), embeddings (key: model + dim + text hash).
- Re-runs are cheap and deterministic. `--doc` limits to one document; `--reset` drops the cache entries for that document.

## 6. Retrieval and answer generation (Next.js `/api/chat`)

Request (zod-validated): `{ question: string (≤ 1000 chars), scope: "gpo"|"gep"|"imac"|"all", history: {role, content}[] (last 6), options?: {vectorOnly?, noRerank?, noRouter?, noFootnotes?} }`. `options` are accepted only when `NODE_ENV !== "production"`.

1. **Condense** (`condense.ts`) — only if `history` is non-empty: Gemini rewrites the question as standalone.
2. **Route** (`router.ts`)
   - Deterministic regexes first: `chapter \d`, `box \d\.\d`, `executive summary`, `statistical annex`, region names + aliases (EAP, ECA, LAC, MNA/MENAAP, SAR, SSA). An explicit match → **hard filter** on matching `section_id`s (including descendants).
   - Then Gemini structured output, given in-scope section titles: `{docs[], sections[], intent: "fact"|"summary"|"comparison"|"out_of_scope", keywords[]}`. Sections here are a **soft boost** only.
   - In `all` scope the router chooses documents (fallback: all three).
3. **Hybrid search** (`retrieve.ts`, single SQL statement)
   - Vector top 40 by cosine distance (HNSW) ∪ keyword top 40 by `ts_rank_cd(tsv, websearch_to_tsquery('english', q))`.
   - Fused with reciprocal rank fusion, k = 60; +0.2 × max-RRF bonus for chunks in router-boosted sections.
   - `intent = summary` → also fetch `section_summary` chunks for the targeted sections.
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

**Response:** streamed text + final data part `{ sources: [{id, doc, sectionPath, page, kind, snippet}], warnings: [], debug: {router, candidates: [{chunkId, vecRank, kwRank, rrf, rerank}], tokens} }`.

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

Each entry: `{id, question, scope, type: brief|coverage|trap|isolation, must_contain[], expected_pages[], expected_section?, rubric?[]}`. `must_contain` and `rubric` values are taken from the actual source pages during implementation — never written from memory.

- **Brief (9)**
  - GPO: depository libraries visited in FY 2018 · system that replaced FDsys and when fully deployed · printing procured for agencies in FY 2018.
  - GEP: South Asia growth outlook · Chapter 3 on sovereign debt in developing economies · Executive Summary main risks.
  - iMac: energy use vs ENERGY STAR requirement · total carbon footprint and use vs manufacturing share · stand material and recycled percentage.
- **Coverage (4)** — iMac sleep power at 230V (`0.59W`, table in endnote 18) · iMac carbon split (`50%`, `45%`, p10) · one GEP Statistical Annex forecast row and one GEP Box question, both picked from the source pages in sub-project 8.
- **Trap (4)** — iMac battery life · GPO budget in FY 2022 · GEP growth outlook for Antarctica · one off-topic question (e.g. "Who won the 2022 FIFA World Cup?").
- **Isolation (1)** — East Asia and Pacific growth outlook: all cited sections must be EAP (or annex EAP rows); no figure that appears only in the South Asia section.

### 8.2 Scorecard (`npm run eval`)

Per question: **retrieval hit** (an expected page/section among final sources) · **fact match** (all `must_contain` present after normalization) · **citation precision** (cited pages ⊆ expected pages) · **verifier clean** · **trap refused** (refusal message, no numbers). The two broad GEP questions additionally get a Gemini judge scoring rubric coverage and unsupported claims. Results are written to `eval/results/<timestamp>.json` and printed as a table.

### 8.3 Ablations

`npm run eval -- --ablate` runs the set with `vectorOnly`, `noRerank`, `noRouter`, `noFootnotes` and prints a side-by-side table against the full pipeline, showing what each technique contributes per document shape.

## 9. Testing

- **Python (pytest, offline)** — footnote linking (iMac p7 marker 18 → note 18 text) · boilerplate stripping · GEP Contents parsing and page-offset mapping (`Risks to the outlook` → PDF p52) · chunker invariants (no chunk crosses a section; size bounds; tables never split mid-row) · vision cross-check using fixture JSON · number normalization.
- **Post-ingest (`ingest/validate.py`)** — GEP has 6 region sections with page ranges inside p81–106 · iMac has table chunks containing `0.59W` and `45%` · GPO chunks contain `govinfo` · every chunk has a non-empty embedding of `EMBED_DIM`.
- **TypeScript (vitest)** — router regexes · RRF fusion · number normalization and verifier · prompt/context assembly · one SQL integration test against the Docker DB.
- **End-to-end** — the eval scorecard (§8) is the acceptance test.

## 10. Delivery breakdown

The work is split into sub-projects. Each gets its own implementation plan in `docs/superpowers/plans/`, is built and verified before the next starts, and is broken into small tasks with one short commit each.

| # | Sub-project | Plan file | Done when |
|---|-------------|-----------|-----------|
| 1 | Foundation — Docker Postgres+pgvector, schema, `.env.example`, Python and Next.js skeletons, Gemini client wrappers | `01-foundation.md` | `docker compose up` works, schema applies, both skeletons run, a Gemini embed call succeeds |
| 2 | Shared ingestion core — parsing, boilerplate, tables, footnotes, chunk helpers, embed, store, cache, report, CLI | `02-ingest-core.md` | Unit tests pass; a dummy profile ingests a PDF end-to-end |
| 3 | GPO narrative profile | `03-gpo-narrative.md` | GPO ingested; validate.py GPO checks pass |
| 4 | GEP hierarchical profile — section tree, contents parsing, boxes, summaries | `04-gep-hierarchical.md` | GEP ingested; section tree and region checks pass |
| 5 | iMac visual profile — vision transcription, cross-check, endnotes | `05-imac-visual.md` | iMac ingested; table/footnote checks pass |
| 6 | Retrieval API — condense, router, hybrid SQL, rerank, context, refusal, generation, verifier | `06-retrieval-api.md` | vitest passes; `/api/chat` answers via curl with sources |
| 7 | Chat UI — selector, streaming, citations drawer, PDF links, debug toggle, warnings | `07-chat-ui.md` | Manual walkthrough of all 3 documents in the browser |
| 8 | Evaluation — question set, scorecard, judge, ablations | `08-evaluation.md` | Pass bar in §1 met; ablation table produced |

Sub-projects 3–5 depend on 2; 6 depends on at least one ingested document; 7 depends on 6; 8 depends on 6 (and fills `must_contain` from source pages early so it can guide 3–6).

**Commit convention:** short, imperative, plain English, ≤ 50 characters, one logical change per commit (e.g. `Add pgvector schema`, `Link iMac endnotes to chunks`).

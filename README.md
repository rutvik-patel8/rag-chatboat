# RAG Chatbot

Document-aware RAG over three differently shaped PDFs (narrative, long hierarchical, visual).
Design: `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`.

## Demo

[`docs/demo/rag-chatbot-demo.mp4`](docs/demo/rag-chatbot-demo.mp4) (2:39, narrated; subtitles in
[`rag-chatbot-demo.srt`](docs/demo/rag-chatbot-demo.srt)): the architecture, the per-document ingestion profiles and
the answer pipeline, then the live app: cited answers, the sources drawer, retrieval scores, follow-ups, refusals and
the evaluation results.

## Setup

1. `cp .env.example .env` and set `GEMINI_API_KEY`.
2. Start Postgres + pgvector (localhost:5433):
   ```bash
   docker compose up -d --wait db
   ```
3. Changed `db/schema.sql`? Wipe and re-create the DB (deletes all data):
   ```bash
   db/reset.sh
   ```
   `EMBED_DIM` in `.env` must equal the `vector(N)` size in `db/schema.sql`; to change it, edit both, then run `db/reset.sh`.

## Ingestion (Python)

Run from `ingest/` (uses [uv](https://docs.astral.sh/uv/)):

```bash
uv sync                          # install dependencies
uv run python doctor.py          # check DB + Gemini are reachable and agree with .env
uv run python ingest.py          # ingest all three PDFs (or: --doc imac, repeatable)
uv run python ingest.py --doc imac --reset   # re-run without cached embeddings/vision/summaries
uv run python validate.py        # post-ingest checks for every stored document
uv run pytest                    # unit + DB integration tests (DB must be running)
uv run pytest -m live            # tests that call the real Gemini API
```

## Web app (Next.js)

Run from `web/` (`web/.env` is a symlink to the root `.env`):

```bash
npm install
npm run dev                  # http://localhost:3100  (health: /api/health)
npm test                     # unit tests
npm run test:int             # + DB integration tests (DB must be running)
npm run test:live            # + tests that call the real Gemini API
```

## Chat API

`POST /api/chat` with `{"question": "...", "scope": "gpo" | "gep" | "imac" | "all", "history": [...]}` streams
newline-delimited JSON events: `sources`, then `text` deltas, then `done` (warnings, debug) — or `error`.

```bash
curl -sN -X POST localhost:3100/api/chat -H 'content-type: application/json' \
  -d '{"question":"What is the growth outlook for South Asia?","scope":"gep"}'
```

Answers use `GEMINI_CHAT_MODEL`; reranking and follow-up rewriting use `GEMINI_UTILITY_MODEL`
(default: `GEMINI_SUMMARY_MODEL`, then the chat model).

## Chat UI

```bash
cd web && npm run dev   # http://localhost:3100
```

Pick a document (or "All documents") and ask a question, or click one of the starter questions. `[S#]` chips open
the sources drawer: the passage the answer used, its section and page, and an "Open page" link to the PDF.
"Show retrieval" shows the router's filters and each candidate's vector, keyword and rerank scores; ⚠ marks figures
the verifier could not find in the cited sources.

## Evaluation

```bash
cd web
npm run eval                            # 18 questions on the full pipeline (Flash answers), pass bar of spec §1
npm run eval -- --only gep-south-asia   # a subset (no pass bar)
npm run eval -- --ablate                # vectorOnly / noRerank / noRouter / noFootnotes vs full, on Flash-Lite
npm run eval -- --ablate --config noRerank
```

Questions live in `eval/questions.yaml`; every expected fact was copied from the PDF page it cites. Each run prints a
scorecard (retrieval hit, fact match, citation precision, verifier, trap refusal, isolation, rubric judge) and writes
`eval/results/<timestamp>.json` (git-ignored). A main run costs ~14 Flash answers of the free tier's ~20 a day.

### Results (2026-10-15 and 2026-10-19)

Models: answers `gemini-3.8-flash` (main run) / `gemini-3.5-flash-lite` (ablations); rerank, judge and condense
`gemini-3.5-flash-lite`; embeddings `gemini-embedding-001` (768 dims).

Main run, 2026-10-19 (`eval/results/2026-10-19T04-58-28-739Z.json`):

```
question             type       hit  facts  cited  verifier  refused  isolated  rubric  result
gpo-libraries        brief      ✓    ✓      100%   ✓         –        –         –       PASS
gpo-govinfo          brief      ✓    ✓      100%   ✓         –        –         –       PASS
gpo-procurement      brief      ✓    ✓      100%   ✓         –        –         –       PASS
gep-south-asia       brief      ✓    ✓      100%   ✓         –        –         –       PASS
gep-chapter-3        brief      ✓    ✓      100%   ✓         –        –         7/7     PASS
gep-exec-risks       brief      ✓    ✓      100%   ✓         –        –         7/7     PASS
imac-energy-star     brief      ✓    ✓      100%   ✗         –        –         –       PASS
imac-carbon          brief      ✓    ✓      100%   ✗         –        –         –       PASS
imac-stand           brief      ✓    ✓      100%   ✓         –        –         –       PASS
imac-sleep-230v      coverage   ✓    ✓      100%   ✓         –        –         –       PASS
imac-carbon-split    coverage   ✓    ✓      50%    ✓         –        –         –       PASS
gep-annex-bhutan     coverage   ✓    ✓      100%   ✓         –        –         –       PASS
gep-box-ai           coverage   ✓    ✓      100%   ✓         –        –         –       PASS
trap-imac-battery    trap       –    –      –      –         ✓        –         –       PASS
trap-gpo-2022        trap       –    –      –      –         ✓        –         –       PASS
trap-gep-antarctica  trap       –    –      –      –         ✓        –         –       PASS
trap-world-cup       trap       –    –      –      –         ✓        –         –       PASS
isolation-eap        isolation  ✓    ✓      100%   ✓         –        ✓         –       PASS

✓ Brief questions answered with citations: 9/9
✓ Trap questions refused: 4/4
✓ Region isolation: 1/1
✗ Answers with every figure verified: 12/14
✓ Questions answered without errors: 18/18
PASS BAR NOT MET
```

The two verifier failures were false positives: both answers named a footnote ("Footnote 18 states…", "per footnote
10"), and the verifier counted the footnote number as a figure. Commit `a973d96` treats "footnote N", "Table N.N",
"Box N.N", "Figure N.N" and "Chapter N" as labels; re-running those two questions after the fix
(`2026-10-19T05-01-47-870Z.json`) gives ✓ verifier for both, with the footnote still mentioned. A full re-run
(~14 Flash answers) is needed to print `PASS BAR MET` for the whole set.

Ablations, 2026-10-15, Flash-Lite answers (`2026-10-15T13-10-45-594Z-ablate.json`), questions passed; re-scored from the
saved answers with the scoring fixes of the evaluation review (as first printed: noRerank 17/18, noRouter 16/18):

```
config       GPO  GEP  iMac  traps  all
full (lite)  3/3  6/6  5/5   4/4    18/18
vectorOnly   3/3  6/6  5/5   4/4    18/18
noRerank     3/3  6/6  5/5   4/4    18/18
noRouter     3/3  5/6  5/5   4/4    17/18
noFootnotes  3/3  6/6  5/5   4/4    18/18
```

- **vectorOnly** (no keyword search): no change on this set.
- **noRerank** (no relevance gate): no change; all four traps were still refused by the prompt's refusal rule.
- **noRouter** (no section filters): the Executive Summary risks answer drew on Chapter 1 and dropped "downside";
  the East Asia answer retrieved, but did not cite, chapter-wide overview sections.
- **noFootnotes**: no change in passes; the East Asia answer picked up a verifier warning.

On 18 questions the full pipeline passes everything and only the router shows a measurable effect; a larger, harder
question set would be needed to measure keyword search, reranking and footnotes.

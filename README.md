# RAG Chatbot

Document-aware RAG over three differently shaped PDFs (narrative, long hierarchical, visual).
Design: `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`.

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

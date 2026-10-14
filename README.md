# RAG Chatbot

Document-aware RAG over three differently shaped PDFs (narrative, long hierarchical, visual).
Design: `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`.

## Setup

1. `cp .env.example .env` and set `GEMINI_API_KEY`.
2. Start Postgres + pgvector (localhost:5433):
   ```bash
   docker compose up -d --wait db
   ```
3. Changed `db/schema.sql` or `EMBED_DIM`? Wipe and re-create the DB (deletes all data):
   ```bash
   db/reset.sh
   ```

## Ingestion (Python)

Run from `ingest/` (uses [uv](https://docs.astral.sh/uv/)):

```bash
uv sync                      # install dependencies
uv run python doctor.py      # check DB + Gemini are reachable and agree with .env
uv run pytest                # unit + DB integration tests (DB must be running)
uv run pytest -m live        # tests that call the real Gemini API
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

# Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the shared infrastructure every later sub-project builds on: Postgres + pgvector with the agreed schema, a Python ingestion skeleton and a Next.js skeleton that both read one `.env`, and Gemini embedding wrappers on both sides with a health check that proves everything is wired up.

**Architecture:** One Docker container runs Postgres 16 + pgvector and applies `db/schema.sql` on first start. `ingest/` is a uv-managed Python project (config, DB helpers, Gemini embedder, `doctor.py` health check). `web/` is a Next.js App Router app (env validation, `pg` pool, schema check behind `/api/health`, Gemini query embedder). Both sides load the repo-root `.env` and must agree on embedding model + dimension; the schema check enforces it.

**Tech Stack:** Docker Compose, `pgvector/pgvector:pg16`, Python 3.10 + uv, `google-genai`, `psycopg[binary]`, `pgvector`, pytest · Node 20, Next.js 16.4, TypeScript, `@google/genai`, `pg`, `zod`, vitest.

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md` (this plan implements §10 row 1, using §3, §4 and §7).

## Global Constraints

- Postgres runs from `pgvector/pgvector:pg16` on host port **5433** (5432 is taken by a local Postgres). Next.js dev runs on port **3100** (3000 is taken).
- `db/schema.sql` is the only contract between Python and TypeScript; its content is spec §4 verbatim.
- Embeddings: `gemini-embedding-001`, output dimensionality **768**, task types `RETRIEVAL_DOCUMENT` (ingest) / `RETRIEVAL_QUERY` (queries). Gemini only normalizes 3072-dim output, so **both embedders L2-normalize every vector**.
- Model IDs come only from `.env`: `GEMINI_CHAT_MODEL=gemini-3.8-flash`, `GEMINI_EMBED_MODEL=gemini-embedding-001`, `EMBED_DIM=768`. Never hard-code them.
- Gemini calls retry **3 attempts** with exponential backoff (1s, 2s), only on HTTP 429/500/502/503/504 or network errors; other errors fail immediately.
- `GEMINI_API_KEY` is server-side only — never prefixed `NEXT_PUBLIC_`, never imported from a client component.
- Python: run every Python command from `ingest/` via `uv run`. Node: run every npm command from `web/`.
- Commits: one per task, short imperative plain English ≤ 50 chars, ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Blank or missing `.env` values** (e.g. `GEMINI_API_KEY=` left empty after copying `.env.example`) → a single error naming every bad variable, not a crash deep inside an SDK. Pinned by tests in Task 2 (Python) and Task 5 (TS).
2. **`EMBED_DIM` changed after the DB volume was created** (init scripts never re-run, so the column stays `vector(768)`) → doctor and `/api/health` report the mismatch and point to `db/reset.sh`, instead of failing later on insert. Pinned in Task 4 and Task 6.
3. **Docker DB not running** → doctor prints `Database check failed: …` and `/api/health` returns 503 with a readable reason; Node's `ECONNREFUSED` errors often have an *empty* `message`, so the reason must fall back to the error code. Pinned in Task 4 and Task 6.
4. **Gemini rate limit vs bad key/model** → 429/503 are retried with backoff; 400 (invalid key, unknown model) fails on the first attempt with the API's message. Pinned in Task 3 and Task 7.
5. **Empty input and large batches** → `[]` returns `[]` without an API call, blank strings raise a clear error, Python splits >100 texts into batches of 100, TypeScript rejects >100. Pinned in Task 3 and Task 7.

---

### Task 1: Postgres + pgvector with schema

**Files:**
- Create: `docker-compose.yml`
- Create: `db/schema.sql`
- Create: `db/reset.sh`
- Create: `.env.example`
- Create: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a running DB at `postgresql://rag:rag@localhost:5433/rag` with tables `documents`, `sections`, `chunks` (exact columns in spec §4); `.env.example` keys `GEMINI_API_KEY`, `GEMINI_CHAT_MODEL`, `GEMINI_EMBED_MODEL`, `EMBED_DIM`, `DATABASE_URL`.

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    container_name: rag-chatboat-db
    environment:
      POSTGRES_USER: rag
      POSTGRES_PASSWORD: rag
      POSTGRES_DB: rag
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - rag-pgdata:/var/lib/postgresql/data
      - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rag -d rag"]
      interval: 2s
      timeout: 3s
      retries: 30

volumes:
  rag-pgdata:
```

- [ ] **Step 2: Write `db/schema.sql` (spec §4 verbatim)**

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

- [ ] **Step 3: Write `db/reset.sh` and make it executable**

```bash
#!/usr/bin/env bash
# Wipes all data and re-applies db/schema.sql.
# Needed after editing the schema: init scripts only run on an empty volume.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose down -v
docker compose up -d --wait db
```

Run: `chmod +x db/reset.sh`

- [ ] **Step 4: Write `.env.example`, then create your local `.env`**

```dotenv
# Copy to .env and fill in GEMINI_API_KEY. Read by both ingest/ (Python) and web/ (Next.js).
GEMINI_API_KEY=
GEMINI_CHAT_MODEL=gemini-3.8-flash
GEMINI_EMBED_MODEL=gemini-embedding-001
# Must match vector(768) in db/schema.sql
EMBED_DIM=768
DATABASE_URL=postgresql://rag:rag@localhost:5433/rag
```

Run: `cp .env.example .env` and paste a real key into `GEMINI_API_KEY` (`.env` is git-ignored).

- [ ] **Step 5: Start the DB and verify the schema**

Run: `docker compose up -d --wait db`
Expected: `Container rag-chatboat-db Healthy`

Run: `docker compose exec -T db psql -U rag -d rag -Atc "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables WHERE table_schema='public'"`
Expected: `chunks,documents,sections`

Run: `docker compose exec -T db psql -U rag -d rag -Atc "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='chunks'::regclass AND attname='embedding'"`
Expected: `vector(768)`

- [ ] **Step 6: Smoke-test vector + keyword search in a rolled-back transaction**

Run:
```bash
docker compose exec -T db psql -U rag -d rag -At <<'SQL'
BEGIN;
INSERT INTO documents (slug, title, profile, pages, embed_model, embed_dim)
VALUES ('smoke', 'Smoke', 'narrative', 1, 'm', 768);
INSERT INTO chunks (doc_id, seq, kind, content, embed_text, page_start, page_end, embedding)
SELECT id, 0, 'text', 'c', 'iMac uses 58 percent less energy than ENERGY STAR', 1, 1,
       array_fill(0.1::real, ARRAY[768])::vector
FROM documents WHERE slug = 'smoke';
SELECT (embedding <=> array_fill(0.1::real, ARRAY[768])::vector) < 1e-6,
       tsv @@ websearch_to_tsquery('english', 'energy star')
FROM chunks;
ROLLBACK;
SQL
```
Expected: the SELECT line prints `t|t`.

- [ ] **Step 7: Write `README.md`**

````markdown
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
````

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml db/schema.sql db/reset.sh .env.example README.md
git commit -m "Add pgvector database and schema" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Python project, settings and DB helpers

**Files:**
- Create: `ingest/pyproject.toml`
- Create: `ingest/config.py`
- Create: `ingest/db.py`
- Test: `ingest/tests/test_config.py`
- Test: `ingest/tests/test_db.py`

**Interfaces:**
- Consumes: DB and `.env` from Task 1.
- Produces:
  - `config.Settings` (frozen dataclass): `gemini_api_key: str`, `database_url: str`, `chat_model: str`, `embed_model: str`, `embed_dim: int`
  - `config.load_settings(env: Mapping[str, str] | None = None) -> Settings` — `None` loads repo-root `.env` into `os.environ`; raises `config.ConfigError`
  - `config.REPO_ROOT: Path`
  - `db.connect(settings: Settings) -> psycopg.Connection` (pgvector types registered; numpy arrays bind to `vector`)
  - `db.embedding_column_dim(conn) -> int` — raises `RuntimeError` if `chunks.embedding` is missing

- [ ] **Step 1: Write `ingest/pyproject.toml` and install**

```toml
[project]
name = "rag-ingest"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "google-genai>=2.30",
  "psycopg[binary]>=3.3",
  "pgvector>=0.5",
  "numpy>=1.26",
  "python-dotenv>=1.2",
]

[dependency-groups]
dev = ["pytest>=9.1"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-m 'not live'"
markers = [
  "integration: needs the Docker DB (docker compose up -d --wait db)",
  "live: calls the real Gemini API; run with: uv run pytest -m live",
]
```

Run: `cd ingest && uv sync`
Expected: creates `ingest/.venv` and `ingest/uv.lock`, exits 0.

- [ ] **Step 2: Write the failing settings tests**

`ingest/tests/test_config.py`:
```python
import pytest

from config import ConfigError, load_settings

VALID = {
    "GEMINI_API_KEY": "test-key",
    "DATABASE_URL": "postgresql://rag:rag@localhost:5433/rag",
    "GEMINI_CHAT_MODEL": "gemini-3.8-flash",
    "GEMINI_EMBED_MODEL": "gemini-embedding-001",
    "EMBED_DIM": "768",
}


def test_loads_valid_settings():
    settings = load_settings(VALID)
    assert settings.embed_dim == 768
    assert settings.embed_model == "gemini-embedding-001"
    assert settings.database_url.endswith(":5433/rag")


def test_blank_and_missing_values_are_all_named():
    env = {**VALID, "GEMINI_API_KEY": "   "}
    del env["DATABASE_URL"]
    with pytest.raises(ConfigError, match="GEMINI_API_KEY, DATABASE_URL"):
        load_settings(env)


def test_non_integer_dim_is_rejected():
    with pytest.raises(ConfigError, match="EMBED_DIM must be an integer"):
        load_settings({**VALID, "EMBED_DIM": "768d"})


def test_out_of_range_dim_is_rejected():
    with pytest.raises(ConfigError, match="between 128 and 3072"):
        load_settings({**VALID, "EMBED_DIM": "4096"})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ingest && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 4: Write `ingest/config.py`**

```python
"""Settings shared by every ingestion step, loaded from the repo-root .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("GEMINI_API_KEY", "DATABASE_URL", "GEMINI_CHAT_MODEL", "GEMINI_EMBED_MODEL", "EMBED_DIM")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    database_url: str
    chat_model: str
    embed_model: str
    embed_dim: int


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv(REPO_ROOT / ".env")
        env = os.environ
    missing = [key for key in REQUIRED if not env.get(key, "").strip()]
    if missing:
        raise ConfigError(f"Missing required settings in .env: {', '.join(missing)} (see .env.example)")
    try:
        dim = int(env["EMBED_DIM"])
    except ValueError:
        raise ConfigError(f"EMBED_DIM must be an integer, got {env['EMBED_DIM']!r}") from None
    if not 128 <= dim <= 3072:
        raise ConfigError(f"EMBED_DIM must be between 128 and 3072, got {dim}")
    return Settings(
        gemini_api_key=env["GEMINI_API_KEY"].strip(),
        database_url=env["DATABASE_URL"].strip(),
        chat_model=env["GEMINI_CHAT_MODEL"].strip(),
        embed_model=env["GEMINI_EMBED_MODEL"].strip(),
        embed_dim=dim,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 6: Write the failing DB integration tests**

`ingest/tests/test_db.py`:
```python
import numpy as np
import pytest

from config import load_settings
from db import connect, embedding_column_dim

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    connection = connect(load_settings())
    yield connection
    connection.rollback()
    connection.close()


def test_schema_tables_exist(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {"documents", "sections", "chunks"} <= {row[0] for row in rows}


def test_embedding_column_matches_settings(conn):
    assert embedding_column_dim(conn) == load_settings().embed_dim


def test_vector_roundtrip_and_keyword_match(conn):
    dim = load_settings().embed_dim
    doc_id = conn.execute(
        "INSERT INTO documents (slug, title, profile, pages, embed_model, embed_dim) "
        "VALUES ('test-doc', 'Test', 'narrative', 1, 'm', %s) RETURNING id",
        (dim,),
    ).fetchone()[0]
    vec = np.zeros(dim, dtype=np.float32)
    vec[0] = 1.0
    conn.execute(
        "INSERT INTO chunks (doc_id, seq, kind, content, embed_text, page_start, page_end, embedding) "
        "VALUES (%s, 0, 'text', 'c', 'GPO distributed 802,834 copies; ENERGY STAR', 1, 1, %s)",
        (doc_id, vec),
    )
    distance, keyword_hit, stored = conn.execute(
        "SELECT embedding <=> %s, tsv @@ websearch_to_tsquery('english', 'energy star'), embedding "
        "FROM chunks WHERE doc_id = %s",
        (vec, doc_id),
    ).fetchone()
    assert distance == pytest.approx(0.0)
    assert keyword_hit is True
    assert len(stored) == dim
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `cd ingest && uv run pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 8: Write `ingest/db.py`**

```python
"""Postgres helpers. db/schema.sql is the contract shared with the Next.js app."""
from __future__ import annotations

import re

import psycopg
from pgvector.psycopg import register_vector

from config import Settings


def connect(settings: Settings) -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, connect_timeout=5)
    register_vector(conn)
    return conn


def embedding_column_dim(conn: psycopg.Connection) -> int:
    """Dimension declared for chunks.embedding in the live DB, e.g. 768 for vector(768)."""
    row = conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = to_regclass('public.chunks') AND attname = 'embedding'"
    ).fetchone()
    if row is None:
        raise RuntimeError("chunks.embedding not found; apply db/schema.sql with db/reset.sh")
    match = re.fullmatch(r"vector\((\d+)\)", row[0])
    if match is None:
        raise RuntimeError(f"chunks.embedding has unexpected type {row[0]}")
    return int(match.group(1))
```

- [ ] **Step 9: Run all Python tests to verify they pass**

Run: `cd ingest && uv run pytest -v`
Expected: 7 passed (DB container from Task 1 must be running)

- [ ] **Step 10: Commit**

```bash
git add ingest/pyproject.toml ingest/uv.lock ingest/config.py ingest/db.py ingest/tests/test_config.py ingest/tests/test_db.py
git commit -m "Add ingest settings and DB helpers" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Python Gemini embedder with retry

**Files:**
- Create: `ingest/gemini.py`
- Test: `ingest/tests/test_gemini.py`
- Test: `ingest/tests/test_gemini_live.py`

**Interfaces:**
- Consumes: `config.Settings`, `config.load_settings` (Task 2).
- Produces:
  - `gemini.TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]`
  - `gemini.BATCH_SIZE = 100`
  - `gemini.is_retryable(exc: BaseException) -> bool`
  - `gemini.with_retry(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> T` (reused later for vision and summary calls)
  - `gemini.l2_normalize(vec: Sequence[float]) -> list[float]`
  - `gemini.Embedder(client, model: str, dim: int, sleep=time.sleep)` with `.embed(texts: Sequence[str], task_type: TaskType) -> list[list[float]]`
  - `gemini.make_client(settings) -> genai.Client`, `gemini.make_embedder(settings) -> Embedder`

- [ ] **Step 1: Write the failing unit tests**

`ingest/tests/test_gemini.py`:
```python
import math
from types import SimpleNamespace

import httpx
import pytest

from gemini import BATCH_SIZE, Embedder, is_retryable, l2_normalize


class FakeApiError(Exception):
    """Same shape as google.genai.errors.APIError: an int `code` attribute."""

    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class FakeModels:
    def __init__(self, dim, failures=(), wrong_dim=False):
        self.dim = dim
        self.failures = list(failures)
        self.wrong_dim = wrong_dim
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=list(contents), config=config))
        if self.failures:
            raise self.failures.pop(0)
        size = self.dim - 1 if self.wrong_dim else self.dim
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * (size - 2)) for _ in contents]
        )


def make_embedder(dim=4, **kwargs):
    models = FakeModels(dim, **kwargs)
    sleeps = []
    embedder = Embedder(SimpleNamespace(models=models), "gemini-embedding-001", dim, sleep=sleeps.append)
    return embedder, models, sleeps


def test_vectors_are_l2_normalised():
    embedder, _, _ = make_embedder()
    [vec] = embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")
    assert vec == pytest.approx([0.6, 0.8, 0.0, 0.0])
    assert math.isclose(sum(v * v for v in vec), 1.0)


def test_request_carries_model_task_type_and_dimension():
    embedder, models, _ = make_embedder()
    embedder.embed(["hello"], "RETRIEVAL_QUERY")
    call = models.calls[0]
    assert call.model == "gemini-embedding-001"
    assert call.config.task_type == "RETRIEVAL_QUERY"
    assert call.config.output_dimensionality == 4


def test_large_inputs_are_split_into_batches_of_100():
    embedder, models, _ = make_embedder()
    vectors = embedder.embed([f"text {i}" for i in range(250)], "RETRIEVAL_DOCUMENT")
    assert len(vectors) == 250
    assert [len(c.contents) for c in models.calls] == [BATCH_SIZE, BATCH_SIZE, 50]
    assert models.calls[2].contents[0] == "text 200"


def test_empty_list_makes_no_api_call():
    embedder, models, _ = make_embedder()
    assert embedder.embed([], "RETRIEVAL_DOCUMENT") == []
    assert models.calls == []


def test_blank_text_is_rejected_before_calling_the_api():
    embedder, models, _ = make_embedder()
    with pytest.raises(ValueError, match="empty text"):
        embedder.embed(["ok", "  "], "RETRIEVAL_DOCUMENT")
    assert models.calls == []


def test_rate_limit_is_retried_with_backoff():
    embedder, models, sleeps = make_embedder(failures=[FakeApiError(429)])
    assert len(embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")) == 1
    assert len(models.calls) == 2
    assert sleeps == [1.0]


def test_bad_request_fails_immediately():
    embedder, models, sleeps = make_embedder(failures=[FakeApiError(400)])
    with pytest.raises(FakeApiError):
        embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")
    assert len(models.calls) == 1
    assert sleeps == []


def test_gives_up_after_three_attempts():
    embedder, models, sleeps = make_embedder(failures=[FakeApiError(503)] * 3)
    with pytest.raises(FakeApiError):
        embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")
    assert len(models.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_wrong_dimension_from_api_is_an_error():
    embedder, _, _ = make_embedder(wrong_dim=True)
    with pytest.raises(RuntimeError, match="4 dims"):
        embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")


def test_zero_vector_cannot_be_normalised():
    with pytest.raises(ValueError):
        l2_normalize([0.0, 0.0])


@pytest.mark.parametrize(
    "exc, expected",
    [
        (FakeApiError(429), True),
        (FakeApiError(503), True),
        (FakeApiError(400), False),
        (FakeApiError(403), False),
        (ConnectionError("reset"), True),
        (httpx.ConnectTimeout("timed out"), True),
        (ValueError("bad"), False),
    ],
)
def test_is_retryable(exc, expected):
    assert is_retryable(exc) is expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ingest && uv run pytest tests/test_gemini.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gemini'`

- [ ] **Step 3: Write `ingest/gemini.py`**

```python
"""Thin wrapper over google-genai: batched, L2-normalised embeddings with retry."""
from __future__ import annotations

import math
import time
from typing import Callable, Literal, Sequence, TypeVar

import httpx
from google import genai
from google.genai import types

from config import Settings

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
BATCH_SIZE = 100
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
T = TypeVar("T")


def is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)  # google.genai.errors.APIError carries the HTTP status here
    if isinstance(code, int):
        return code in RETRYABLE_STATUS
    return isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError))


def with_retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        raise ValueError("cannot normalise a zero vector")
    return [v / norm for v in vec]


class Embedder:
    def __init__(self, client, model: str, dim: int, sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._dim = dim
        self._sleep = sleep

    def embed(self, texts: Sequence[str], task_type: TaskType) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise ValueError("cannot embed empty text")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(self._embed_batch(list(texts[start : start + BATCH_SIZE]), task_type))
        return vectors

    def _embed_batch(self, batch: list[str], task_type: TaskType) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._dim)
        response = with_retry(
            lambda: self._client.models.embed_content(model=self._model, contents=batch, config=config),
            sleep=self._sleep,
        )
        values = [embedding.values for embedding in response.embeddings]
        if len(values) != len(batch) or any(len(v) != self._dim for v in values):
            raise RuntimeError(f"expected {len(batch)} embeddings of {self._dim} dims from {self._model}")
        return [l2_normalize(v) for v in values]


def make_client(settings: Settings) -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def make_embedder(settings: Settings) -> Embedder:
    return Embedder(make_client(settings), settings.embed_model, settings.embed_dim)
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd ingest && uv run pytest tests/test_gemini.py -v`
Expected: 17 passed

- [ ] **Step 5: Write the live test (real Gemini call, opt-in)**

`ingest/tests/test_gemini_live.py`:
```python
import math

import pytest

from config import load_settings
from gemini import make_embedder

pytestmark = pytest.mark.live


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))  # inputs are already unit length


def test_real_embeddings_are_normalised_and_meaningful():
    settings = load_settings()
    embedder = make_embedder(settings)
    doc, unrelated = embedder.embed(
        ["GPO distributed 802,834 copies of 4,211 titles.", "The iMac stand is made of recycled aluminum."],
        "RETRIEVAL_DOCUMENT",
    )
    [query] = embedder.embed(["How many copies did GPO distribute?"], "RETRIEVAL_QUERY")
    assert len(doc) == settings.embed_dim
    assert math.isclose(sum(v * v for v in doc), 1.0, rel_tol=1e-6)
    assert cosine(query, doc) > cosine(query, unrelated)
```

- [ ] **Step 6: Run the live test**

Run: `cd ingest && uv run pytest -m live -v`
Expected: 1 passed. If it fails with HTTP 400/403, fix `GEMINI_API_KEY` or `GEMINI_EMBED_MODEL` in `.env` and re-run.

- [ ] **Step 7: Commit**

```bash
git add ingest/gemini.py ingest/tests/test_gemini.py ingest/tests/test_gemini_live.py
git commit -m "Add Gemini embedder with retry" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Python doctor check

**Files:**
- Create: `ingest/doctor.py`
- Test: `ingest/tests/test_doctor.py`
- Modify: `README.md` (append an "Ingestion (Python)" section)

**Interfaces:**
- Consumes: `config.load_settings`, `config.ConfigError`, `config.Settings`, `db.connect`, `db.embedding_column_dim`, `gemini.make_embedder`.
- Produces:
  - `doctor.Check` dataclass: `name: str`, `ok: bool`, `detail: str`
  - `doctor.check_database(settings, connect_fn=db.connect, dim_fn=db.embedding_column_dim) -> Check`
  - `doctor.check_gemini(settings, embedder_factory=gemini.make_embedder) -> Check`
  - CLI: `uv run python doctor.py` exits 0 when all checks pass, 1 otherwise.

- [ ] **Step 1: Write the failing tests**

`ingest/tests/test_doctor.py`:
```python
from contextlib import nullcontext

from config import Settings
from doctor import check_database, check_gemini

SETTINGS = Settings(
    gemini_api_key="k",
    database_url="postgresql://rag:rag@localhost:5433/rag",
    chat_model="gemini-3.8-flash",
    embed_model="gemini-embedding-001",
    embed_dim=768,
)


def fake_connect(_settings):
    return nullcontext(object())


def test_database_ok():
    check = check_database(SETTINGS, connect_fn=fake_connect, dim_fn=lambda _c: 768)
    assert check.ok
    assert "vector(768)" in check.detail


def test_database_unreachable_is_reported_not_raised():
    def refuse(_settings):
        raise OSError("connection refused")

    check = check_database(SETTINGS, connect_fn=refuse)
    assert not check.ok
    assert check.detail == "Database check failed: connection refused"


def test_dimension_mismatch_points_to_reset():
    check = check_database(SETTINGS, connect_fn=fake_connect, dim_fn=lambda _c: 1536)
    assert not check.ok
    assert "vector(1536)" in check.detail and "EMBED_DIM is 768" in check.detail
    assert "db/reset.sh" in check.detail


class FakeEmbedder:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def embed(self, texts, task_type):
        if self.error:
            raise self.error
        return self.result


def test_gemini_ok():
    check = check_gemini(SETTINGS, embedder_factory=lambda _s: FakeEmbedder(result=[[0.1] * 768]))
    assert check.ok
    assert check.detail == "gemini-embedding-001 returned 768 dims"


def test_gemini_failure_is_reported_not_raised():
    check = check_gemini(SETTINGS, embedder_factory=lambda _s: FakeEmbedder(error=RuntimeError("API key not valid")))
    assert not check.ok
    assert check.detail == "Embedding call failed: API key not valid"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ingest && uv run pytest tests/test_doctor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'doctor'`

- [ ] **Step 3: Write `ingest/doctor.py`**

```python
"""Checks that the DB and Gemini are reachable and agree with .env.

Run from ingest/:  uv run python doctor.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from config import ConfigError, Settings, load_settings
from db import connect, embedding_column_dim
from gemini import make_embedder


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_database(settings: Settings, connect_fn=connect, dim_fn=embedding_column_dim) -> Check:
    try:
        with connect_fn(settings) as conn:
            dim = dim_fn(conn)
    except Exception as exc:
        return Check("database", False, f"Database check failed: {exc}")
    if dim != settings.embed_dim:
        return Check(
            "database",
            False,
            f"chunks.embedding is vector({dim}) but EMBED_DIM is {settings.embed_dim}; run db/reset.sh",
        )
    return Check("database", True, f"schema ok, chunks.embedding is vector({dim})")


def check_gemini(settings: Settings, embedder_factory=make_embedder) -> Check:
    try:
        [vector] = embedder_factory(settings).embed(["health check"], "RETRIEVAL_QUERY")
    except Exception as exc:
        return Check("gemini", False, f"Embedding call failed: {exc}")
    return Check("gemini", True, f"{settings.embed_model} returned {len(vector)} dims")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"✗ config: {exc}")
        return 1
    checks = [check_database(settings), check_gemini(settings)]
    for check in checks:
        print(f"{'✓' if check.ok else '✗'} {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ingest && uv run pytest -v`
Expected: 29 passed (4 config + 3 db + 17 gemini + 5 doctor)

- [ ] **Step 5: Run the doctor against the real services**

Run: `cd ingest && uv run python doctor.py`
Expected:
```
✓ database: schema ok, chunks.embedding is vector(768)
✓ gemini: gemini-embedding-001 returned 768 dims
```

Then confirm the failure path: `docker compose stop db && (cd ingest && uv run python doctor.py); docker compose start db`
Expected: `✗ database: Database check failed: …connection…refused…`, exit code 1, Gemini line still `✓`.

- [ ] **Step 6: Append to `README.md`**

````markdown

## Ingestion (Python)

Run from `ingest/` (uses [uv](https://docs.astral.sh/uv/)):

```bash
uv sync                      # install dependencies
uv run python doctor.py      # check DB + Gemini are reachable and agree with .env
uv run pytest                # unit + DB integration tests (DB must be running)
uv run pytest -m live        # tests that call the real Gemini API
```
````

- [ ] **Step 7: Commit**

```bash
git add ingest/doctor.py ingest/tests/test_doctor.py README.md
git commit -m "Add ingest doctor check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Next.js app with env validation

**Files:**
- Create: `web/` (via `create-next-app`)
- Modify: `web/package.json` (scripts, deps)
- Modify: `web/next.config.ts`
- Create: `web/vitest.config.ts`
- Create: `web/lib/env.ts`
- Test: `web/lib/env.test.ts`

**Interfaces:**
- Consumes: repo-root `.env` (Task 1).
- Produces:
  - `@/lib/env`: `type Env = { GEMINI_API_KEY: string; DATABASE_URL: string; GEMINI_CHAT_MODEL: string; GEMINI_EMBED_MODEL: string; EMBED_DIM: number }`, `class ConfigError extends Error`, `parseEnv(source: Record<string, string | undefined>): Env`, `getEnv(): Env` (cached, reads `process.env`)
  - npm scripts: `dev` (port 3100), `test` (unit), `test:int` (sets `RUN_INTEGRATION=1`), `test:live` (sets `RUN_LIVE=1`)
  - Test convention: integration tests use `describe.skipIf(!process.env.RUN_INTEGRATION)`, live tests use `describe.skipIf(!process.env.RUN_LIVE)`.

- [ ] **Step 1: Scaffold the app**

Run (from repo root):
```bash
npx --yes create-next-app@16.4.0 web --ts --app --tailwind --eslint --no-src-dir --import-alias "@/*" --use-npm --yes
```
Expected: `web/` created with `app/`, `package.json`, `tsconfig.json`, `next.config.ts`. If it created `web/.git`, delete it: `rm -rf web/.git` (the repo root is already a git repo).

- [ ] **Step 2: Install dependencies**

Run:
```bash
cd web && npm install @google/genai@^2.29 pg@^8.23 zod@^4.6 && npm install -D vitest@^5.0 @types/pg @next/env@16.4.0
```
Expected: exits 0.

- [ ] **Step 3: Set scripts in `web/package.json`**

Replace the `"scripts"` block with:
```json
"scripts": {
  "dev": "next dev -p 3100",
  "build": "next build",
  "start": "next start -p 3100",
  "lint": "eslint",
  "test": "vitest run",
  "test:int": "RUN_INTEGRATION=1 vitest run",
  "test:live": "RUN_LIVE=1 vitest run"
}
```

- [ ] **Step 4: Load the repo-root `.env` in `web/next.config.ts`**

Replace the file with:
```ts
import path from "node:path";
import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

// One .env at the repo root is shared with the Python ingestion side.
loadEnvConfig(path.resolve(process.cwd(), ".."));

const nextConfig: NextConfig = {};

export default nextConfig;
```

- [ ] **Step 5: Write `web/vitest.config.ts`**

```ts
import path from "node:path";
import { loadEnvConfig } from "@next/env";
import { defineConfig } from "vitest/config";

loadEnvConfig(path.resolve(__dirname, ".."));

export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname) } },
  test: {
    environment: "node",
    include: ["**/*.test.ts"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
```

- [ ] **Step 6: Write the failing env tests**

`web/lib/env.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { ConfigError, parseEnv } from "@/lib/env";

const VALID = {
  GEMINI_API_KEY: "test-key",
  DATABASE_URL: "postgresql://rag:rag@localhost:5433/rag",
  GEMINI_CHAT_MODEL: "gemini-3.8-flash",
  GEMINI_EMBED_MODEL: "gemini-embedding-001",
  EMBED_DIM: "768",
};

describe("parseEnv", () => {
  it("parses valid settings and coerces EMBED_DIM to a number", () => {
    const env = parseEnv(VALID);
    expect(env.EMBED_DIM).toBe(768);
    expect(env.GEMINI_EMBED_MODEL).toBe("gemini-embedding-001");
  });

  it("names every blank or missing variable in one error", () => {
    const { DATABASE_URL: _omit, ...rest } = VALID;
    const run = () => parseEnv({ ...rest, GEMINI_API_KEY: "   " });
    expect(run).toThrow(ConfigError);
    expect(run).toThrow("GEMINI_API_KEY, DATABASE_URL");
  });

  it("rejects a non-numeric or out-of-range EMBED_DIM", () => {
    expect(() => parseEnv({ ...VALID, EMBED_DIM: "abc" })).toThrow("EMBED_DIM");
    expect(() => parseEnv({ ...VALID, EMBED_DIM: "4096" })).toThrow("EMBED_DIM");
  });
});
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `cd web && npm test -- lib/env.test.ts`
Expected: FAIL — cannot resolve `@/lib/env`

- [ ] **Step 8: Write `web/lib/env.ts`**

```ts
import { z } from "zod";

// Server-only settings. None are NEXT_PUBLIC_, so they never reach the browser bundle.
const EnvSchema = z.object({
  GEMINI_API_KEY: z.string().trim().min(1),
  DATABASE_URL: z.string().trim().min(1),
  GEMINI_CHAT_MODEL: z.string().trim().min(1),
  GEMINI_EMBED_MODEL: z.string().trim().min(1),
  EMBED_DIM: z.coerce.number().int().min(128).max(3072),
});

export type Env = z.infer<typeof EnvSchema>;

export class ConfigError extends Error {}

export function parseEnv(source: Record<string, string | undefined>): Env {
  const result = EnvSchema.safeParse(source);
  if (!result.success) {
    const names = [...new Set(result.error.issues.map((issue) => String(issue.path[0])))];
    throw new ConfigError(`Invalid or missing settings in .env: ${names.join(", ")} (see .env.example)`);
  }
  return result.data;
}

let cached: Env | undefined;

export function getEnv(): Env {
  cached ??= parseEnv(process.env);
  return cached;
}
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: 3 passed

- [ ] **Step 10: Check the dev server starts on port 3100**

Run: `cd web && npm run dev` (in a second terminal), then `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3100/`
Expected: `200`. Stop the dev server.

- [ ] **Step 11: Commit**

```bash
git add web
git commit -m "Scaffold Next.js app" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: DB pool, schema check and `/api/health`

**Files:**
- Create: `web/lib/rag/db.ts`
- Create: `web/lib/rag/schema.ts`
- Create: `web/lib/rag/health.ts`
- Create: `web/app/api/health/route.ts`
- Test: `web/lib/rag/schema.test.ts`
- Test: `web/lib/rag/health.test.ts`
- Test: `web/lib/rag/schema.int.test.ts`

**Interfaces:**
- Consumes: `getEnv` from `@/lib/env` (Task 5).
- Produces:
  - `@/lib/rag/db`: `getPool(): Pool` (one pool per process, survives dev hot reload)
  - `@/lib/rag/schema`: `type SchemaFacts = { vectorType: string | null; documents: { slug: string; embed_model: string; embed_dim: number }[] }`, `type ExpectedEmbedding = { embedModel: string; embedDim: number }`, `evaluateSchema(facts: SchemaFacts, expected: ExpectedEmbedding): string[]`, `readSchemaFacts(pool: Pool): Promise<SchemaFacts>`
  - `@/lib/rag/health`: `errorMessage(err: unknown): string`, `type HealthResult = { status: 200 | 500 | 503; body: { ok: boolean; problems: string[]; documents: string[] } }`, `runHealthCheck(readFacts: () => Promise<SchemaFacts>, expected: ExpectedEmbedding): Promise<HealthResult>`
  - `GET /api/health` → `HealthResult.body` with `HealthResult.status`. Sub-project 6 reuses `evaluateSchema` for the startup check in `/api/chat`.

- [ ] **Step 1: Write the failing schema tests**

`web/lib/rag/schema.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { evaluateSchema } from "@/lib/rag/schema";

const expected = { embedModel: "gemini-embedding-001", embedDim: 768 };

describe("evaluateSchema", () => {
  it("accepts a matching column and matching documents", () => {
    const facts = {
      vectorType: "vector(768)",
      documents: [{ slug: "gpo", embed_model: "gemini-embedding-001", embed_dim: 768 }],
    };
    expect(evaluateSchema(facts, expected)).toEqual([]);
  });

  it("reports a missing chunks table", () => {
    const [problem] = evaluateSchema({ vectorType: null, documents: [] }, expected);
    expect(problem).toContain("db/reset.sh");
  });

  it("reports a column dimension that differs from EMBED_DIM", () => {
    const [problem] = evaluateSchema({ vectorType: "vector(1536)", documents: [] }, expected);
    expect(problem).toBe("chunks.embedding is vector(1536) but EMBED_DIM is 768; run db/reset.sh");
  });

  it("reports documents ingested with a different embedding model or dimension", () => {
    const facts = {
      vectorType: "vector(768)",
      documents: [{ slug: "gep", embed_model: "text-embedding-004", embed_dim: 768 }],
    };
    const [problem] = evaluateSchema(facts, expected);
    expect(problem).toContain('"gep"');
    expect(problem).toContain("re-run ingestion");
  });
});
```

- [ ] **Step 2: Write the failing health tests**

`web/lib/rag/health.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { errorMessage, runHealthCheck } from "@/lib/rag/health";

const expected = { embedModel: "gemini-embedding-001", embedDim: 768 };

describe("runHealthCheck", () => {
  it("returns 200 with document slugs when everything matches", async () => {
    const result = await runHealthCheck(
      async () => ({
        vectorType: "vector(768)",
        documents: [{ slug: "gpo", embed_model: "gemini-embedding-001", embed_dim: 768 }],
      }),
      expected,
    );
    expect(result).toEqual({ status: 200, body: { ok: true, problems: [], documents: ["gpo"] } });
  });

  it("returns 500 with problems on a schema mismatch", async () => {
    const result = await runHealthCheck(async () => ({ vectorType: "vector(1536)", documents: [] }), expected);
    expect(result.status).toBe(500);
    expect(result.body.ok).toBe(false);
    expect(result.body.problems[0]).toContain("vector(1536)");
  });

  it("returns 503 when the database is unreachable", async () => {
    const result = await runHealthCheck(async () => {
      throw new Error("connect ECONNREFUSED 127.0.0.1:5433");
    }, expected);
    expect(result.status).toBe(503);
    expect(result.body.problems).toEqual(["Database unavailable: connect ECONNREFUSED 127.0.0.1:5433"]);
  });
});

describe("errorMessage", () => {
  it("falls back to the error code when the message is empty (Node AggregateError)", () => {
    const err = Object.assign(new AggregateError([], ""), { code: "ECONNREFUSED" });
    expect(errorMessage(err)).toBe("ECONNREFUSED");
  });

  it("stringifies non-Error values", () => {
    expect(errorMessage("boom")).toBe("boom");
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd web && npm test -- lib/rag`
Expected: FAIL — cannot resolve `@/lib/rag/schema` and `@/lib/rag/health`

- [ ] **Step 4: Write `web/lib/rag/db.ts`**

```ts
import { Pool } from "pg";
import { getEnv } from "@/lib/env";

// Cache on globalThis so Next.js dev hot reloads don't open a new pool each time.
const globalForPool = globalThis as unknown as { ragPool?: Pool };

export function getPool(): Pool {
  globalForPool.ragPool ??= new Pool({
    connectionString: getEnv().DATABASE_URL,
    max: 5,
    connectionTimeoutMillis: 3000,
  });
  return globalForPool.ragPool;
}
```

- [ ] **Step 5: Write `web/lib/rag/schema.ts`**

```ts
import type { Pool } from "pg";

export type SchemaFacts = {
  vectorType: string | null;
  documents: { slug: string; embed_model: string; embed_dim: number }[];
};

export type ExpectedEmbedding = { embedModel: string; embedDim: number };

/** Problems that would make retrieval silently wrong; empty array means healthy. */
export function evaluateSchema(facts: SchemaFacts, expected: ExpectedEmbedding): string[] {
  const problems: string[] = [];
  if (facts.vectorType === null) {
    problems.push("chunks.embedding not found; apply db/schema.sql with db/reset.sh");
  } else if (facts.vectorType !== `vector(${expected.embedDim})`) {
    problems.push(`chunks.embedding is ${facts.vectorType} but EMBED_DIM is ${expected.embedDim}; run db/reset.sh`);
  }
  for (const doc of facts.documents) {
    if (doc.embed_model !== expected.embedModel || doc.embed_dim !== expected.embedDim) {
      problems.push(
        `document "${doc.slug}" was embedded with ${doc.embed_model}/${doc.embed_dim} but the server uses ` +
          `${expected.embedModel}/${expected.embedDim}; re-run ingestion`,
      );
    }
  }
  return problems;
}

export async function readSchemaFacts(pool: Pool): Promise<SchemaFacts> {
  const column = await pool.query<{ type: string }>(
    `SELECT format_type(atttypid, atttypmod) AS type FROM pg_attribute
     WHERE attrelid = to_regclass('public.chunks') AND attname = 'embedding'`,
  );
  const vectorType = column.rows[0]?.type ?? null;
  if (vectorType === null) return { vectorType, documents: [] };
  const docs = await pool.query<SchemaFacts["documents"][number]>(
    "SELECT slug, embed_model, embed_dim FROM documents ORDER BY slug",
  );
  return { vectorType, documents: docs.rows };
}
```

- [ ] **Step 6: Write `web/lib/rag/health.ts`**

```ts
import { evaluateSchema, type ExpectedEmbedding, type SchemaFacts } from "@/lib/rag/schema";

export type HealthResult = {
  status: 200 | 500 | 503;
  body: { ok: boolean; problems: string[]; documents: string[] };
};

/** Node's connection errors are often AggregateErrors with an empty message but a `code`. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message || (err as { code?: string }).code || err.name;
  return String(err);
}

export async function runHealthCheck(
  readFacts: () => Promise<SchemaFacts>,
  expected: ExpectedEmbedding,
): Promise<HealthResult> {
  let facts: SchemaFacts;
  try {
    facts = await readFacts();
  } catch (err) {
    return {
      status: 503,
      body: { ok: false, problems: [`Database unavailable: ${errorMessage(err)}`], documents: [] },
    };
  }
  const problems = evaluateSchema(facts, expected);
  return {
    status: problems.length === 0 ? 200 : 500,
    body: { ok: problems.length === 0, problems, documents: facts.documents.map((doc) => doc.slug) },
  };
}
```

- [ ] **Step 7: Write `web/app/api/health/route.ts`**

```ts
import { NextResponse } from "next/server";
import { getEnv, type Env } from "@/lib/env";
import { getPool } from "@/lib/rag/db";
import { errorMessage, runHealthCheck } from "@/lib/rag/health";
import { readSchemaFacts } from "@/lib/rag/schema";

export const dynamic = "force-dynamic";

export async function GET() {
  let env: Env;
  try {
    env = getEnv();
  } catch (err) {
    return NextResponse.json({ ok: false, problems: [errorMessage(err)], documents: [] }, { status: 500 });
  }
  const { status, body } = await runHealthCheck(() => readSchemaFacts(getPool()), {
    embedModel: env.GEMINI_EMBED_MODEL,
    embedDim: env.EMBED_DIM,
  });
  return NextResponse.json(body, { status });
}
```

- [ ] **Step 8: Run the unit tests to verify they pass**

Run: `cd web && npm test`
Expected: 12 passed (3 env + 4 schema + 5 health)

- [ ] **Step 9: Write the DB integration test**

`web/lib/rag/schema.int.test.ts`:
```ts
import { afterAll, describe, expect, it } from "vitest";
import { getEnv } from "@/lib/env";
import { getPool } from "@/lib/rag/db";
import { readSchemaFacts } from "@/lib/rag/schema";

describe.skipIf(!process.env.RUN_INTEGRATION)("readSchemaFacts (live DB)", () => {
  afterAll(async () => {
    await getPool().end();
  });

  it("reads the vector column type from the real schema", async () => {
    const facts = await readSchemaFacts(getPool());
    expect(facts.vectorType).toBe(`vector(${getEnv().EMBED_DIM})`);
    expect(Array.isArray(facts.documents)).toBe(true);
  });
});
```

- [ ] **Step 10: Run the integration test**

Run: `cd web && npm run test:int`
Expected: 13 passed (the 12 unit tests + 1 integration)

- [ ] **Step 11: Check the endpoint by hand**

Run: `cd web && npm run dev` (second terminal), then `curl -s -w "\n%{http_code}\n" http://localhost:3100/api/health`
Expected: `{"ok":true,"problems":[],"documents":[]}` and `200`

Run: `docker compose stop db && curl -s -w "\n%{http_code}\n" http://localhost:3100/api/health; docker compose start db`
Expected: `{"ok":false,"problems":["Database unavailable: …"],"documents":[]}` and `503` — the text after the colon must not be empty. Stop the dev server.

- [ ] **Step 12: Commit**

```bash
git add web/lib/rag/db.ts web/lib/rag/schema.ts web/lib/rag/health.ts web/app/api/health/route.ts web/lib/rag/schema.test.ts web/lib/rag/health.test.ts web/lib/rag/schema.int.test.ts
git commit -m "Add DB health endpoint" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: TypeScript query embedder

**Files:**
- Create: `web/lib/rag/gemini.ts`
- Test: `web/lib/rag/gemini.test.ts`
- Test: `web/lib/rag/gemini.live.test.ts`
- Modify: `README.md` (append a "Web app (Next.js)" section)

**Interfaces:**
- Consumes: `getEnv` from `@/lib/env` (Task 5).
- Produces (`@/lib/rag/gemini`):
  - `type EmbedTaskType = "RETRIEVAL_DOCUMENT" | "RETRIEVAL_QUERY"`
  - `MAX_BATCH = 100`
  - `isRetryable(err: unknown): boolean`
  - `withRetry<T>(fn: () => Promise<T>, opts?: { attempts?: number; baseDelayMs?: number; sleep?: (ms: number) => Promise<void> }): Promise<T>` — reused by router/rerank/generation in sub-project 6
  - `l2Normalize(v: number[]): number[]`
  - `type EmbedClient`, `type Embedder = { embed(texts: string[], taskType: EmbedTaskType): Promise<number[][]> }`
  - `createEmbedder(opts: { client: EmbedClient; model: string; dim: number; sleep?: (ms: number) => Promise<void> }): Embedder`
  - `getGenAI(): GoogleGenAI` (shared client), `getEmbedder(): Embedder`, `embedQuery(text: string): Promise<number[]>`

- [ ] **Step 1: Write the failing unit tests**

`web/lib/rag/gemini.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { createEmbedder, isRetryable, l2Normalize, MAX_BATCH, type EmbedClient } from "@/lib/rag/gemini";

class FakeApiError extends Error {
  // Same shape as @google/genai's ApiError: a numeric `status`.
  constructor(public status: number) {
    super(`HTTP ${status}`);
  }
}

function setup({ dim = 4, failures = [] as Error[], wrongDim = false } = {}) {
  const calls: Parameters<EmbedClient["models"]["embedContent"]>[0][] = [];
  const sleeps: number[] = [];
  const client: EmbedClient = {
    models: {
      async embedContent(req) {
        calls.push(req);
        const failure = failures.shift();
        if (failure) throw failure;
        const size = wrongDim ? dim - 1 : dim;
        return { embeddings: req.contents.map(() => ({ values: [3, 4, ...Array(size - 2).fill(0)] })) };
      },
    },
  };
  const embedder = createEmbedder({
    client,
    model: "gemini-embedding-001",
    dim,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  });
  return { embedder, calls, sleeps };
}

describe("createEmbedder", () => {
  it("returns L2-normalised vectors", async () => {
    const { embedder } = setup();
    const [vec] = await embedder.embed(["hello"], "RETRIEVAL_QUERY");
    expect(vec[0]).toBeCloseTo(0.6);
    expect(vec[1]).toBeCloseTo(0.8);
  });

  it("sends model, task type and output dimensionality", async () => {
    const { embedder, calls } = setup();
    await embedder.embed(["hello"], "RETRIEVAL_QUERY");
    expect(calls[0]).toEqual({
      model: "gemini-embedding-001",
      contents: ["hello"],
      config: { taskType: "RETRIEVAL_QUERY", outputDimensionality: 4 },
    });
  });

  it("returns [] for no texts without calling the API", async () => {
    const { embedder, calls } = setup();
    expect(await embedder.embed([], "RETRIEVAL_QUERY")).toEqual([]);
    expect(calls).toHaveLength(0);
  });

  it("rejects blank text and oversized batches before calling the API", async () => {
    const { embedder, calls } = setup();
    await expect(embedder.embed(["  "], "RETRIEVAL_QUERY")).rejects.toThrow("empty text");
    await expect(embedder.embed(Array(MAX_BATCH + 1).fill("x"), "RETRIEVAL_QUERY")).rejects.toThrow("at most 100");
    expect(calls).toHaveLength(0);
  });

  it("retries a 429 with backoff", async () => {
    const { embedder, calls, sleeps } = setup({ failures: [new FakeApiError(429)] });
    await embedder.embed(["hello"], "RETRIEVAL_QUERY");
    expect(calls).toHaveLength(2);
    expect(sleeps).toEqual([1000]);
  });

  it("fails immediately on a 400", async () => {
    const { embedder, calls, sleeps } = setup({ failures: [new FakeApiError(400)] });
    await expect(embedder.embed(["hello"], "RETRIEVAL_QUERY")).rejects.toThrow("HTTP 400");
    expect(calls).toHaveLength(1);
    expect(sleeps).toEqual([]);
  });

  it("gives up after three attempts", async () => {
    const failures = [new FakeApiError(503), new FakeApiError(503), new FakeApiError(503)];
    const { embedder, calls, sleeps } = setup({ failures });
    await expect(embedder.embed(["hello"], "RETRIEVAL_QUERY")).rejects.toThrow("HTTP 503");
    expect(calls).toHaveLength(3);
    expect(sleeps).toEqual([1000, 2000]);
  });

  it("errors when the API returns the wrong dimension", async () => {
    const { embedder } = setup({ wrongDim: true });
    await expect(embedder.embed(["hello"], "RETRIEVAL_QUERY")).rejects.toThrow("4 dims");
  });
});

describe("isRetryable", () => {
  it.each([
    [new FakeApiError(429), true],
    [new FakeApiError(500), true],
    [new FakeApiError(400), false],
    [new FakeApiError(403), false],
    [new TypeError("fetch failed"), true],
    [new Error("other"), false],
  ])("%s -> %s", (err, expected) => {
    expect(isRetryable(err)).toBe(expected);
  });
});

describe("l2Normalize", () => {
  it("rejects a zero vector", () => {
    expect(() => l2Normalize([0, 0])).toThrow();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm test -- lib/rag/gemini.test.ts`
Expected: FAIL — cannot resolve `@/lib/rag/gemini`

- [ ] **Step 3: Write `web/lib/rag/gemini.ts`**

```ts
import { GoogleGenAI } from "@google/genai";
import { getEnv } from "@/lib/env";

export type EmbedTaskType = "RETRIEVAL_DOCUMENT" | "RETRIEVAL_QUERY";
export const MAX_BATCH = 100;
const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);

export function isRetryable(err: unknown): boolean {
  const status = (err as { status?: unknown } | null)?.status; // @google/genai ApiError carries the HTTP status
  if (typeof status === "number") return RETRYABLE_STATUS.has(status);
  return err instanceof TypeError; // fetch network failures surface as TypeError
}

type RetryOptions = { attempts?: number; baseDelayMs?: number; sleep?: (ms: number) => Promise<void> };
const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function withRetry<T>(
  fn: () => Promise<T>,
  { attempts = 3, baseDelayMs = 1000, sleep = realSleep }: RetryOptions = {},
): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt >= attempts || !isRetryable(err)) throw err;
      await sleep(baseDelayMs * 2 ** (attempt - 1));
    }
  }
}

export function l2Normalize(v: number[]): number[] {
  const norm = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0));
  if (norm === 0) throw new Error("cannot normalise a zero vector");
  return v.map((x) => x / norm);
}

export type EmbedClient = {
  models: {
    embedContent(req: {
      model: string;
      contents: string[];
      config: { taskType: EmbedTaskType; outputDimensionality: number };
    }): Promise<{ embeddings?: { values?: number[] }[] }>;
  };
};

export type Embedder = { embed(texts: string[], taskType: EmbedTaskType): Promise<number[][]> };

export function createEmbedder({
  client,
  model,
  dim,
  sleep,
}: {
  client: EmbedClient;
  model: string;
  dim: number;
  sleep?: RetryOptions["sleep"];
}): Embedder {
  return {
    async embed(texts, taskType) {
      if (texts.length === 0) return [];
      if (texts.length > MAX_BATCH) throw new Error(`embed accepts at most ${MAX_BATCH} texts, got ${texts.length}`);
      if (texts.some((text) => text.trim() === "")) throw new Error("cannot embed empty text");
      const response = await withRetry(
        () => client.models.embedContent({ model, contents: texts, config: { taskType, outputDimensionality: dim } }),
        { sleep },
      );
      const vectors = (response.embeddings ?? []).map((embedding) => embedding.values ?? []);
      if (vectors.length !== texts.length || vectors.some((v) => v.length !== dim)) {
        throw new Error(`expected ${texts.length} embeddings of ${dim} dims from ${model}`);
      }
      return vectors.map(l2Normalize);
    },
  };
}

let genAI: GoogleGenAI | undefined;
let embedder: Embedder | undefined;

export function getGenAI(): GoogleGenAI {
  genAI ??= new GoogleGenAI({ apiKey: getEnv().GEMINI_API_KEY });
  return genAI;
}

export function getEmbedder(): Embedder {
  const env = getEnv();
  embedder ??= createEmbedder({ client: getGenAI(), model: env.GEMINI_EMBED_MODEL, dim: env.EMBED_DIM });
  return embedder;
}

export async function embedQuery(text: string): Promise<number[]> {
  const [vector] = await getEmbedder().embed([text], "RETRIEVAL_QUERY");
  return vector;
}
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd web && npm test`
Expected: 27 passed (12 earlier + 8 embedder + 6 isRetryable + 1 l2Normalize). If `tsc`/the IDE reports that `GoogleGenAI` is not assignable to `EmbedClient`, change the `client` field type in `createEmbedder` to `EmbedClient | GoogleGenAI` and cast inside: `(client as EmbedClient).models.embedContent(...)`.

- [ ] **Step 5: Write the live test**

`web/lib/rag/gemini.live.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { getEnv } from "@/lib/env";
import { embedQuery } from "@/lib/rag/gemini";

describe.skipIf(!process.env.RUN_LIVE)("embedQuery (real Gemini)", () => {
  it("returns a unit-length vector of EMBED_DIM", async () => {
    const vec = await embedQuery("What is the growth outlook for South Asia?");
    expect(vec).toHaveLength(getEnv().EMBED_DIM);
    expect(Math.sqrt(vec.reduce((s, x) => s + x * x, 0))).toBeCloseTo(1, 5);
  });
});
```

- [ ] **Step 6: Run the live test and the type check**

Run: `cd web && npm run test:live -- lib/rag/gemini.live.test.ts`
Expected: 1 passed.

Run: `cd web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Append to `README.md`**

````markdown

## Web app (Next.js)

Run from `web/`:

```bash
npm install
npm run dev                  # http://localhost:3100  (health: /api/health)
npm test                     # unit tests
npm run test:int             # + DB integration tests (DB must be running)
npm run test:live            # + tests that call the real Gemini API
```
````

- [ ] **Step 8: Commit**

```bash
git add web/lib/rag/gemini.ts web/lib/rag/gemini.test.ts web/lib/rag/gemini.live.test.ts README.md
git commit -m "Add query embedder" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Sub-project acceptance (spec §10, row 1)

Run all of these from a clean shell; every line must hold before starting `02-ingest-core.md`.

- [ ] `docker compose up -d --wait db` → container healthy
- [ ] `cd ingest && uv run pytest` → 29 passed; `uv run pytest -m live` → 1 passed
- [ ] `cd ingest && uv run python doctor.py` → two `✓` lines, exit 0
- [ ] `cd web && npm run test:int` → 28 passed (27 unit + 1 DB); `npm run test:live` → 28 passed (27 unit + 1 Gemini)
- [ ] `cd web && npx tsc --noEmit && npm run lint` → no errors
- [ ] `npm run dev` + `curl localhost:3100/api/health` → `{"ok":true,…}` with HTTP 200
- [ ] `git log --oneline` shows 7 new short commits, one per task

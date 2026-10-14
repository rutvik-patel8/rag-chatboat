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

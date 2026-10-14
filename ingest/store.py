"""Write one document's sections and chunks in a single transaction (spec §5.1 step 7)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from model import ChunkDraft, SectionDraft


@dataclass(frozen=True)
class DocumentMeta:
    slug: str
    title: str
    profile: str
    pages: int


@dataclass(frozen=True)
class StoreStats:
    sections: int
    chunks: int


class MixedEmbeddingError(RuntimeError):
    pass


def store_document(
    conn: psycopg.Connection,
    meta: DocumentMeta,
    sections: list[SectionDraft],
    chunks: list[ChunkDraft],
    embed_texts: list[str],
    vectors: list[list[float]],
    embed_model: str,
    embed_dim: int,
) -> StoreStats:
    if not (len(chunks) == len(embed_texts) == len(vectors)):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors and {len(embed_texts)} embed texts")
    try:
        others = conn.execute(
            "SELECT slug, embed_model, embed_dim FROM documents WHERE slug <> %s", (meta.slug,)
        ).fetchall()
        mixed = [slug for slug, model, dim in others if (model, dim) != (embed_model, embed_dim)]
        if mixed:
            raise MixedEmbeddingError(
                f"documents {', '.join(mixed)} use a different embedding model/dim than "
                f"{embed_model}/{embed_dim}; re-ingest them or fix .env"
            )
        conn.execute("DELETE FROM documents WHERE slug = %s", (meta.slug,))
        doc_id = conn.execute(
            "INSERT INTO documents (slug, title, profile, pages, embed_model, embed_dim) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (meta.slug, meta.title, meta.profile, meta.pages, embed_model, embed_dim),
        ).fetchone()[0]
        section_ids: dict[str, int] = {}
        for section in sorted(sections, key=lambda s: s.level):  # parents before children
            parent_id = section_ids[section.parent_key] if section.parent_key else None
            section_ids[section.key] = conn.execute(
                "INSERT INTO sections (doc_id, parent_id, title, level, path, kind, page_start, page_end, content) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (doc_id, parent_id, section.title, section.level, section.path, section.kind,
                 section.page_start, section.page_end, section.content),
            ).fetchone()[0]
        seq: dict[str, int] = defaultdict(int)
        rows = []
        for chunk, text, vector in zip(chunks, embed_texts, vectors):
            rows.append(
                (doc_id, section_ids[chunk.section_key], seq[chunk.section_key], chunk.kind, chunk.content, text,
                 chunk.page_start, chunk.page_end, Jsonb(chunk.footnotes), Jsonb(chunk.metadata),
                 np.asarray(vector, dtype=np.float32))
            )
            seq[chunk.section_key] += 1
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (doc_id, section_id, seq, kind, content, embed_text, page_start, page_end, "
                "footnotes, metadata, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return StoreStats(len(sections), len(chunks))

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
    assert len(stored.to_list()) == dim  # pgvector >= 0.5 returns a Vector object

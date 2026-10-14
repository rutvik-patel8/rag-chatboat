import pytest

from config import load_settings
from db import connect
from model import ChunkDraft, SectionDraft
from store import DocumentMeta, MixedEmbeddingError, store_document

pytestmark = pytest.mark.integration


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def conn(settings):
    connection = connect(settings)
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    yield connection
    connection.rollback()
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    connection.close()


def unit_vector(dim, hot=0):
    vec = [0.0] * dim
    vec[hot] = 1.0
    return vec


def sample(dim):
    sections = [
        SectionDraft("ch2/sar", "South Asia", 2, "Ch. 2 › South Asia", 95, 98, parent_key="ch2", kind="region"),
        SectionDraft("ch2", "Ch. 2 Regional Outlooks", 1, "Ch. 2", 69, 106, parent_key="root"),
        SectionDraft("root", "Test doc", 0, "", 1, 200),
    ]
    chunks = [
        ChunkDraft("ch2/sar", "text", "Growth in SAR is projected to slow.", 95, 95, footnotes=[{"n": 1, "text": "Fiscal years."}]),
        ChunkDraft("ch2/sar", "table", "| Country | 2026f |", 96, 96),
        ChunkDraft("ch2", "text", "Regional overview.", 69, 69),
    ]
    texts = [f"embed {c.content}" for c in chunks]
    return sections, chunks, texts, [unit_vector(dim, i) for i in range(len(chunks))]


def store(conn, settings, slug="test-store", **overrides):
    sections, chunks, texts, vectors = sample(settings.embed_dim)
    args = dict(sections=sections, chunks=chunks, embed_texts=texts, vectors=vectors)
    args.update(overrides)
    meta = DocumentMeta(slug, "Test doc", "plain", 200)
    return store_document(conn, meta, embed_model=settings.embed_model, embed_dim=settings.embed_dim, **args)


def test_stores_sections_with_parents_and_chunks_with_seq(conn, settings):
    stats = store(conn, settings)
    assert (stats.sections, stats.chunks) == (3, 3)
    rows = conn.execute(
        "SELECT s.path, p.title, c.seq, c.kind, c.footnotes, c.embed_text "
        "FROM chunks c JOIN sections s ON s.id = c.section_id LEFT JOIN sections p ON p.id = s.parent_id "
        "JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-store' ORDER BY c.id"
    ).fetchall()
    assert rows == [
        ("Ch. 2 › South Asia", "Ch. 2 Regional Outlooks", 0, "text", [{"n": 1, "text": "Fiscal years."}], "embed Growth in SAR is projected to slow."),
        ("Ch. 2 › South Asia", "Ch. 2 Regional Outlooks", 1, "table", [], "embed | Country | 2026f |"),
        ("Ch. 2", "Test doc", 0, "text", [], "embed Regional overview."),
    ]


def test_restoring_replaces_instead_of_duplicating(conn, settings):
    store(conn, settings)
    store(conn, settings)
    assert conn.execute("SELECT count(*) FROM documents WHERE slug = 'test-store'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-store'"
    ).fetchone()[0] == 3


def test_failed_store_keeps_the_previous_version(conn, settings):
    store(conn, settings)
    sections, chunks, texts, vectors = sample(settings.embed_dim)
    chunks[0].section_key = "missing-section"
    with pytest.raises(KeyError):
        store(conn, settings, chunks=chunks)
    assert conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-store'"
    ).fetchone()[0] == 3


def test_refuses_to_mix_embedding_models(conn, settings):
    conn.execute(
        "INSERT INTO documents (slug, title, profile, pages, embed_model, embed_dim) "
        "VALUES ('test-other', 'Other', 'plain', 1, 'some-other-model', %s)",
        (settings.embed_dim,),
    )
    conn.commit()
    with pytest.raises(MixedEmbeddingError, match="test-other"):
        store(conn, settings)


def test_vector_count_mismatch_is_rejected_before_writing(conn, settings):
    with pytest.raises(ValueError, match="3 chunks but 2 vectors"):
        store(conn, settings, vectors=[unit_vector(settings.embed_dim)] * 2)
    assert conn.execute("SELECT count(*) FROM documents WHERE slug = 'test-store'").fetchone()[0] == 0

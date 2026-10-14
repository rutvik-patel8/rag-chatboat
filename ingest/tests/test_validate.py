import pytest

from config import load_settings
from db import connect
from model import ChunkDraft, SectionDraft
from store import DocumentMeta, store_document
from validate import generic_checks

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    settings = load_settings()
    connection = connect(settings)
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    store_document(
        connection,
        DocumentMeta("test-validate", "Validate", "plain", 1),
        [SectionDraft("root", "Validate", 0, "", 1, 1)],
        [ChunkDraft("root", "text", "Some content.", 1, 1)],
        ["Validate | p.1 | text\n\nSome content."],
        [vec],
        settings.embed_model,
        settings.embed_dim,
    )
    yield connection
    connection.rollback()
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    connection.close()


def test_good_document_passes_every_generic_check(conn):
    results = generic_checks(conn, load_settings(), "test-validate")
    assert results and all(r.ok for r in results), [r for r in results if not r.ok]


def test_empty_chunk_content_is_reported(conn):
    conn.execute(
        "UPDATE chunks SET content = '  ' WHERE doc_id = (SELECT id FROM documents WHERE slug = 'test-validate')"
    )
    conn.commit()
    failed = [r.name for r in generic_checks(conn, load_settings(), "test-validate") if not r.ok]
    assert failed == ["no empty chunks"]

from dataclasses import replace

import pytest

from chunk import plain
from config import load_settings
from db import connect
from pipeline import run
from profiles import DOCS

pytestmark = pytest.mark.integration


class FakeEmbedder:
    def __init__(self, dim):
        self.dim = dim

    def embed(self, texts, task_type):
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]


@pytest.fixture
def conn():
    connection = connect(load_settings())
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    yield connection
    connection.rollback()
    connection.execute("DELETE FROM documents WHERE slug LIKE 'test-%'")
    connection.commit()
    connection.close()


def test_imac_ingests_end_to_end_with_footnotes(conn, tmp_path):
    settings = load_settings()
    spec = replace(DOCS["imac"], slug="test-imac")
    report = run(spec, plain.build, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path)

    assert report.notes == 19
    assert report.chunks_by_kind["footnote"] == 19
    assert report.unresolved == []
    stored = conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'test-imac'"
    ).fetchone()[0]
    assert stored == sum(report.chunks_by_kind.values())

    content, footnotes = conn.execute(
        "SELECT c.content, c.footnotes FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.slug = 'test-imac' AND c.kind = 'text' AND c.content LIKE %s",
        ("%iMac consumes 58 percent less energy%",),
    ).fetchone()
    assert "ENERGY STAR.18" not in content
    assert any(f["n"] == 18 and "ENERGY STAR Program Requirements" in f["text"] for f in footnotes)

    leftovers = conn.execute(
        "SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.slug = 'test-imac' AND c.content LIKE %s",
        ("%iMac | Product Environmental Report%",),
    ).fetchone()[0]
    assert leftovers == 0

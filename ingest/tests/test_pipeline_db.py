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


def test_summaries_are_added_only_when_the_spec_asks(conn, tmp_path):
    from model import ChunkDraft

    settings = load_settings()
    calls = []

    def summarizer(spec, sections):
        calls.append(spec.slug)
        root = next(s for s in sections if s.level == 0)
        return [ChunkDraft(root.key, "section_summary", "Summary of iMac: recycled aluminum stand.", 1, 12)]

    spec = replace(DOCS["imac"], slug="test-imac")
    run(spec, plain.build, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path, summarizer)
    assert calls == []
    report = run(replace(spec, summaries=True), plain.build, settings, conn, FakeEmbedder(settings.embed_dim), tmp_path, summarizer)
    assert calls == ["test-imac"]
    assert report.chunks_by_kind["section_summary"] == 1

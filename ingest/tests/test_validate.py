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


def test_default_checks_every_known_document_and_missing_ones_fail(conn):
    from validate import main, slugs_to_check

    assert slugs_to_check(None) == ["gpo", "gep", "imac"]
    assert main(["--doc", "test-missing"]) == 1
    assert main(["--doc", "test-validate"]) == 0


def store_gpo_like(conn, slug, with_table=True, with_footer=False):
    settings = load_settings()
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    sections = [
        SectionDraft("root", "GPO", 0, "", 2, 82),
        SectionDraft("msg", "A Message from the Acting Deputy Director", 1, "A Message from the Acting Deputy Director", 4, 6, "root"),
        SectionDraft("otg", "GPO and Open, Transparent Government", 1, "GPO and Open, Transparent Government", 19, 23, "root"),
        SectionDraft("gi", "govinfo", 2, "GPO and Open, Transparent Government › govinfo", 22, 23, "otg"),
        SectionDraft("num", "GPO By the Numbers FY 2018", 1, "GPO By the Numbers FY 2018", 14, 15, "root"),
        SectionDraft("fs", "SECTION IV FINANCIAL STATEMENTS", 1, "SECTION IV FINANCIAL STATEMENTS", 57, 82, "root"),
        SectionDraft("mc", "15. Major Customers", 2, "SECTION IV FINANCIAL STATEMENTS › 15. Major Customers", 81, 81, "fs"),
    ]
    chunks = [
        ChunkDraft("msg", "text", "in FY 2018 GPO staff visited 166 depository libraries in 25 states", 5, 5),
        ChunkDraft("msg", "text", "was the full-scale deployment of govinfo, our public-facing repository", 4, 4),
        ChunkDraft("msg", "text", "GPO procured more than $375 million worth of printing", 5, 5),
    ]
    chunks += [ChunkDraft("msg", "caption", f"Caption {i}.", 5, 5) for i in range(15)]
    chunks.append(ChunkDraft("mc", "text", "Department of State $ 270,596 33.3%", 81, 81))
    if with_footer:
        chunks.append(ChunkDraft("fs", "text", "U. S. Government Publishing Office | Financial Year In Review – FY 2018", 60, 60))
    if with_table:
        chunks += [ChunkDraft("num", "table", "| Revenues from procured printing | $375.7 million |", 14, 14) for _ in range(4)]
    store_document(
        conn, DocumentMeta(slug, "GPO", "narrative", 82), sections, chunks,
        [c.content for c in chunks], [vec] * len(chunks), settings.embed_model, settings.embed_dim,
    )


def test_gpo_checks_pass_on_a_document_with_the_brief_facts(conn):
    from validate import DOC_CHECKS

    store_gpo_like(conn, "test-gpo")
    results = [check(conn, "test-gpo") for check in DOC_CHECKS["gpo"]]
    assert len(results) == 9
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_gpo_checks_report_a_missing_table_fact(conn):
    from validate import DOC_CHECKS

    store_gpo_like(conn, "test-gpo", with_table=False)
    failed = [r.name for r in (check(conn, "test-gpo") for check in DOC_CHECKS["gpo"]) if not r.ok]
    assert failed == ["table chunk with '$375.7 million' in GPO By the Numbers FY 2018", "at least 4 table chunks"]


def test_gpo_checks_report_a_leaked_footer(conn):
    from validate import DOC_CHECKS

    store_gpo_like(conn, "test-gpo", with_footer=True)
    failed = [r.name for r in (check(conn, "test-gpo") for check in DOC_CHECKS["gpo"]) if not r.ok]
    assert failed == ["no chunk with 'Financial Year In Review'"]

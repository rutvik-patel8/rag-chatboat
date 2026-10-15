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

GEP_REGIONS = [
    ("eap", "East Asia and Pacific", 81, 83),
    ("eca", "Europe and Central Asia", 84, 87),
    ("lac", "Latin America and the Caribbean", 88, 91),
    ("mna", "Middle East, North Africa, Afghanistan, and Pakistan", 92, 95),
    ("sar", "South Asia", 96, 98),
    ("ssa", "Sub-Saharan Africa", 99, 102),
]


def store_gep_like(conn, slug, regions=GEP_REGIONS):
    settings = load_settings()
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    ch2, ch1, ch3 = "Ch. 2 Regional Outlooks", "Ch. 1 Global Outlook", "Ch. 3 A Rising Challenge: Sovereign Debt"
    sections = [
        SectionDraft("root", "GEP", 0, "", 15, 191),
        SectionDraft("es", "Executive Summary", 1, "Executive Summary", 19, 21, "root", "front"),
        SectionDraft("c1", ch1, 1, ch1, 23, 62, "root"),
        SectionDraft("b11", "BOX 1.1 How much will AI affect global growth?", 2, f"{ch1} › BOX 1.1 How much will AI affect global growth?", 30, 35, "c1", "box"),
        SectionDraft("refs", "References", 2, f"{ch1} › References", 63, 63, "c1"),
        SectionDraft("c2", ch2, 1, ch2, 69, 105, "root"),
        SectionDraft("c3", ch3, 1, ch3, 107, 134, "root"),
    ]
    sections += [SectionDraft(key, title, 2, f"{ch2} › {title}", a, b, "c2", "region") for key, title, a, b in regions]
    sections.append(SectionDraft("sar-out", "Outlook", 3, f"{ch2} › South Asia › Outlook", 96, 97, "sar"))
    chunks = [
        ChunkDraft("sar-out", "text", "Growth in SAR is expected to soften to 6.3 percent in 2026, mainly", 96, 96),
        ChunkDraft("sar-out", "table", "TABLE 2.9 South Asia forecast summary\n\n| x |", 98, 98),
        ChunkDraft("es", "text", "The global economy is facing another major shock.", 19, 19),
        ChunkDraft("c3", "section_summary", f"Summary of {ch3}: debt.", 107, 134),
    ]
    chunks += [ChunkDraft("c1", "chart", f"FIGURE 1.{i}", 27, 27) for i in range(40)]
    chunks += [ChunkDraft("c1", "table", f"TABLE 1.{i}", 26, 26) for i in range(100)]
    chunks += [ChunkDraft("c1", "section_summary", f"Summary {i}", 23, 62) for i in range(29)]
    store_document(
        conn, DocumentMeta(slug, "GEP", "hierarchical", 200), sections, chunks,
        [c.content for c in chunks], [vec] * len(chunks), settings.embed_model, settings.embed_dim,
    )


def test_gep_checks_pass_on_a_document_shaped_like_the_report(conn):
    from validate import DOC_CHECKS

    store_gep_like(conn, "test-gep")
    results = [check(conn, "test-gep") for check in DOC_CHECKS["gep"]]
    assert len(results) == 10
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_gep_checks_report_a_misplaced_region(conn):
    from validate import DOC_CHECKS

    regions = [r if r[1] != "South Asia" else ("sar", "South Asia", 95, 98) for r in GEP_REGIONS[:5]]
    store_gep_like(conn, "test-gep", regions=regions)
    failed = [r.name for r in (check(conn, "test-gep") for check in DOC_CHECKS["gep"]) if not r.ok]
    assert failed == ["6 region sections within p81–106"]


def store_imac_like(conn, slug, energy_footnote=True):
    settings = load_settings()
    vec = [1.0] + [0.0] * (settings.embed_dim - 1)
    sections = [
        SectionDraft("root", "iMac", 0, "", 1, 12),
        SectionDraft("use", "Use", 1, "Use", 7, 7, "root"),
        SectionDraft("cf", "Carbon Footprint", 1, "Carbon Footprint", 10, 10, "root"),
        SectionDraft("en", "Endnotes", 1, "Endnotes", 11, 12, "root"),
    ]
    note18 = [{"n": 18, "text": "Energy consumption values are based on ENERGY STAR."}] if energy_footnote else []
    chunks = [
        ChunkDraft("use", "text", "iMac uses 58 percent less energy than the requirement for ENERGY STAR.", 7, 7, footnotes=note18),
        ChunkDraft("use", "text", "iMac uses 100% recycled aluminum in the stand.", 1, 1),
        ChunkDraft("cf", "table", "| Product use | 45% |", 10, 10),
        ChunkDraft("en", "table", "| Sleep | 0.56W | 0.57W | 0.59W |", 12, 12),
    ]
    chunks += [ChunkDraft("cf", "chart", f"Values (measured from the chart's shapes): Use: 156 kg (45%) {i}", 2, 2) for i in range(3)]
    chunks += [ChunkDraft("cf", "callout", f"76% recycled content {i}", 6, 6) for i in range(3)]
    chunks += [ChunkDraft("en", "footnote", f"Footnote {i}: text", 11, 11) for i in range(1, 20)]
    store_document(
        conn, DocumentMeta(slug, "iMac", "visual", 12), sections, chunks,
        [c.content for c in chunks], [vec] * len(chunks), settings.embed_model, settings.embed_dim,
    )


def test_imac_checks_pass_on_a_document_with_the_brief_facts(conn):
    from validate import DOC_CHECKS

    store_imac_like(conn, "test-imac")
    results = [check(conn, "test-imac") for check in DOC_CHECKS["imac"]]
    assert len(results) == 8
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_imac_checks_report_a_missing_footnote_link(conn):
    from validate import DOC_CHECKS

    store_imac_like(conn, "test-imac", energy_footnote=False)
    failed = [r.name for r in (check(conn, "test-imac") for check in DOC_CHECKS["imac"]) if not r.ok]
    assert failed == ["chunk with '58 percent less energy than the requirement for ENERGY STAR' citing footnote 18"]

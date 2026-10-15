import pytest

from chunk.common import count_tokens
from chunk.hierarchical import build
from pipeline import attach_footnotes, prepare
from profiles import DOCS, PROFILES
from report import Report

CH2 = "Ch. 2 Regional Outlooks"


@pytest.fixture(scope="module")
def built():
    prepared = prepare(DOCS["gep"])
    result = build(DOCS["gep"], prepared)
    return result, {s.key: s for s in result.sections}, prepared


def chunks_with(built, needle):
    result, by_key, _ = built
    return [(c.kind, by_key[c.section_key].path) for c in result.chunks if needle in c.content]


def test_gep_uses_the_hierarchical_profile_with_summaries():
    spec = DOCS["gep"]
    assert (spec.profile, spec.summaries) == ("hierarchical", True)
    assert spec.skip_pages == frozenset(range(1, 15)) | frozenset(range(192, 201))
    assert "hierarchical" in PROFILES


def test_level_one_sections(built):
    assert [s.title for s in built[0].sections if s.level == 1] == [
        "Acknowledgments",
        "Foreword",
        "Executive Summary",
        "Abbreviations",
        "Ch. 1 Global Outlook",
        CH2,
        "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs",
        "Ch. 4 Navigating Volatility: Fiscal Policy and Commodity Price Swings",
        "Statistical Annex",
    ]


def test_six_regions_with_their_real_pages(built):
    regions = [(s.title, s.page_start, s.page_end, s.path) for s in built[0].sections if s.kind == "region"]
    assert [(t, a, b) for t, a, b, _ in regions] == [
        ("East Asia and Pacific", 81, 83),
        ("Europe and Central Asia", 84, 87),
        ("Latin America and the Caribbean", 88, 91),
        ("Middle East, North Africa, Afghanistan, and Pakistan", 92, 95),
        ("South Asia", 96, 98),
        ("Sub-Saharan Africa", 99, 102),
    ]
    assert all(path.startswith(f"{CH2} › ") for *_, path in regions)


def test_boxes_and_their_own_text(built):
    boxes = [s.title for s in built[0].sections if s.kind == "box"]
    assert boxes == [
        "BOX 1.1 How much will AI affect global growth?",
        "BOX 1.2 Unlocking private investment",
        "BOX 2.1 Low-income countries: Recent developments and outlook",
        "BOX 3.1 The costs of hidden debt",
    ]
    box_path = "Ch. 1 Global Outlook › BOX 1.1 How much will AI affect global growth?"
    assert f"{box_path} › From task-level gains to economy-wide productivity" in [s.path for s in built[0].sections]
    assert all(path.startswith(box_path) for kind, path in chunks_with(built, "Most studies focus on the United States"))


def test_south_asia_outlook_text_table_and_figure(built):
    sar = f"{CH2} › South Asia"
    assert ("text", f"{sar} › Outlook") in chunks_with(built, "Growth in SAR is expected to soften to 6.3 percent in 2026")
    tables = [p for k, p in chunks_with(built, "TABLE 2.9 South Asia forecast summary") if k == "table"]
    assert tables and all(p.startswith(sar) for p in tables)
    assert [k for k, p in chunks_with(built, "FIGURE 2.7 SAR: Outlook") if p.startswith(sar)] == ["chart"]


def test_wrapped_headings_merge_at_every_level(built):
    paths = [s.path for s in built[0].sections]
    assert "Ch. 1 Global Outlook › Major economies: Recent developments and outlook" in paths
    assert (
        "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs › Impact of EMDE government debt on "
        "interest rates › Non-linear relationship between debt and interest rates"
    ) in paths
    assert not {"interest rates", "the Caribbean", "and data"} & {s.title for s in built[0].sections}


def test_references_are_in_the_tree_but_not_chunked(built):
    result, by_key, _ = built
    references = [s for s in result.sections if s.title == "References"]
    assert len(references) == 4
    assert not [c for c in result.chunks if by_key[c.section_key].title == "References"]


def test_statistical_annex_pages_are_tables(built):
    annex = [k for k, p in chunks_with(built, "| World | 2.8 | 2.9 | 2.9 | 2.5 | 2.8 | 2.8 | 3.1 |")]
    assert annex == ["table"]
    result, by_key, _ = built
    assert sum(by_key[c.section_key].path == "Statistical Annex › Real GDP growth" and c.kind == "table" for c in result.chunks) >= 4


def test_executive_summary_text(built):
    assert ("text", "Executive Summary") in chunks_with(built, "The global economy is facing another major shock")


def test_every_page_footnote_reaches_a_body_chunk(built):
    result, _, prepared = built
    attach_footnotes(result.chunks, prepared.notes, Report("gep", "GEP"))
    cited = {(f["n"], f["text"]) for c in result.chunks for f in c.footnotes}
    assert cited == {(n.n, n.text) for n in prepared.notes.notes}
    assert not [c for c in result.chunks if c.kind == "table" and c.refs]


def test_chunks_stay_inside_their_sections_and_budgets(built):
    result, by_key, _ = built
    for chunk in result.chunks:
        section = by_key[chunk.section_key]
        assert section.page_start <= chunk.page_start <= chunk.page_end <= section.page_end
        assert count_tokens(chunk.content) <= 1000
    kinds = [c.kind for c in result.chunks]
    assert kinds.count("text") >= 300 and kinds.count("table") >= 120 and kinds.count("chart") >= 50
    assert len(result.sections) >= 130


def section(built, path_end):
    return next(s for s in built[0].sections if s.path.endswith(path_end))


def test_two_column_pages_are_read_column_by_column(built):
    ch1, ch4 = "Ch. 1 Global Outlook", "Ch. 4 Navigating Volatility: Fiscal Policy and Commodity Price Swings"
    paths = [s.path for s in built[0].sections]
    assert f"{ch1} › Policy challenges › Key global challenges › Diversifying and deepening trade and economic partnerships" in paths
    assert f"{ch4} › Policy frameworks for fiscal management › Evolution of fiscal policy frameworks" in paths
    contribution = section(built, "› Contribution of rising government debt to EMDE interest rates")
    assert len(contribution.content or "") > 1000 and "basis points" in contribution.content


def test_front_matter_text_lands_in_its_own_section(built):
    foreword, acknowledgments = section(built, "Foreword"), section(built, "Acknowledgments")
    assert "Global growth this year will slump" in foreword.content
    assert "Global growth this year will slump" not in (acknowledgments.content or "")
    assert not [c for c in built[0].chunks if c.section_key == "root"]

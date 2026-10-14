import pytest

from chunk.common import count_tokens
from chunk.narrative import build
from pipeline import prepare
from profiles import DOCS


@pytest.fixture(scope="module")
def built():
    spec = DOCS["gpo"]
    result = build(spec, prepare(spec))
    return result, {s.key: s for s in result.sections}


def hits(built, needle):
    result, by_key = built
    return [(c.kind, by_key[c.section_key].path) for c in result.chunks if needle in c.content]


def test_gpo_uses_the_narrative_profile_and_skips_covers():
    assert DOCS["gpo"].profile == "narrative"
    assert DOCS["gpo"].skip_pages == frozenset({1, 31, 32, 33})


def test_section_tree_has_the_expected_titles(built):
    paths = [s.path for s in built[0].sections]
    for path in [
        "Letter of Transmittal",
        "A Message from the Acting Deputy Director",
        "About GPO",
        "About GPO › History",
        "GPO and Congress",
        "GPO By the Numbers FY 2018",
        "GPO and Open, Transparent Government › Federal Depository Library Program",
        "GPO and Open, Transparent Government › govinfo",
        "SECTION I MANAGEMENT DISCUSSION AND ANALYSIS (UNAUDITED)",
        "SECTION IV FINANCIAL STATEMENTS › Consolidated Balance Sheets As of September 30, 2018 and 2017",
    ]:
        assert path in paths, path
    assert not {"U.S. Government Publishing Office", "Restricted:", "Unrestricted", "SECTION I", "MANAGEMENT"} & {
        s.title for s in built[0].sections
    }


def test_brief_facts_land_in_the_right_sections(built):
    assert ("text", "A Message from the Acting Deputy Director") in hits(built, "visited 166 depository libraries")
    assert ("text", "GPO and Open, Transparent Government › Federal Depository Library Program") in hits(
        built, "visited 166 depository libraries"
    )
    assert hits(built, "full-scale deployment of govinfo") == [("text", "A Message from the Acting Deputy Director")]
    assert hits(built, "procured more than $375 million") == [("text", "A Message from the Acting Deputy Director")]
    assert ("table", "GPO By the Numbers FY 2018") in hits(built, "$375.7 million")


def test_captions_are_separate_chunks(built):
    result, _ = built
    caption = "(Above) GPO employees producing the Congressional Record."
    assert [kind for kind, _ in hits(built, caption)] == ["caption"]
    assert sum(c.kind == "caption" for c in result.chunks) == 20


def test_statement_tables_are_aligned_and_not_repeated_as_text(built):
    result, _ = built
    tables = [c for c in result.chunks if c.kind == "table"]
    assert [c.page_start for c in tables] == [14, 58, 58, 59, 60, 60]
    assert any("| Fund balance with Treasury (Note 2) | $644,807 | $585,883 |" in c.content for c in tables)
    assert all(c.content.startswith("| (Dollars in Thousands) |") for c in tables if c.page_start >= 58)
    assert not hits(built, "Fund balance with Treasury (Note 2)") or all(k == "table" for k, _ in hits(built, "Fund balance with Treasury (Note 2)"))


def test_drop_cap_and_number_hyphen_are_repaired(built):
    assert ("text", "About GPO") in hits(built, "The U.S. Government Publishing Office (GPO), a legislative branch agency")
    assert hits(built, "Public Law 103-40")
    assert not hits(built, "103- 40")


def test_chunks_stay_inside_their_section_and_size(built):
    result, by_key = built
    for chunk in result.chunks:
        section = by_key[chunk.section_key]
        assert section.page_start <= chunk.page_start <= chunk.page_end <= section.page_end
        assert count_tokens(chunk.content) <= 600
    assert sum(c.kind == "text" for c in result.chunks) >= 100
    assert len(result.sections) >= 100
    assert by_key[next(s.key for s in result.sections if s.path.endswith("› govinfo"))].content


def test_note_tables_stay_under_their_own_note(built):
    customers = hits(built, "270,596")
    assert ("text", "SECTION IV FINANCIAL STATEMENTS › 15. Major Customers") in customers
    assert not [path for _, path in customers if "16. Subsequent Events" in path]
    assert ("text", "SECTION IV FINANCIAL STATEMENTS › 3. Accounts Receivable, Net") in hits(built, "Total accounts receivable, net")


def test_financial_footer_and_page_numbers_are_stripped(built):
    import re

    result, _ = built
    assert not hits(built, "Financial Year In Review")
    lone_numbers = [
        line for c in result.chunks if c.kind == "table" for line in c.content.splitlines() if re.fullmatch(r"\| \d+ \|(\s+\|)*", line)
    ]
    assert lone_numbers == []


def test_oig_memo_and_letter_spaced_cover_open_no_sections(built):
    titles = {s.title for s in built[0].sections}
    assert not {"Date", "To", "From", "U.S. GOVERNMENT PUBLISHING OFFICE"} & titles
    assert not [t for t in titles if t.startswith("A U D I T")]


def test_statement_year_header_sits_over_the_value_columns(built):
    tables = [c.content for c in built[0].chunks if c.kind == "table" and c.page_start >= 58]
    assert tables and all("\n|  | 2018 | 2017 |\n" in t for t in tables)

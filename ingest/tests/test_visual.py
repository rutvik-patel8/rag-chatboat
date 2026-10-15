import json
from pathlib import Path

import pytest

from chunk.visual import APPROXIMATE, block_content, build, page_title, table_notes, verify
from enrich.vision import clean_block
from helpers import page_of
from model import Note
from pipeline import prepare
from profiles import DOCS, PROFILES

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "imac_vision.json").read_text())


def described(fail_on=()):
    def describe(page_number):
        if page_number in fail_on:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return [clean_block(b) for b in FIXTURE[str(page_number)]]

    return describe


@pytest.fixture(scope="module")
def imac():
    prepared = prepare(DOCS["imac"])
    prepared.describe = described()
    result = build(DOCS["imac"], prepared)
    return result, {s.key: s for s in result.sections}, prepared


def test_imac_uses_the_visual_profile_with_vision():
    assert (DOCS["imac"].profile, DOCS["imac"].vision) == ("visual", True)
    assert "visual" in PROFILES


def test_page_titles_join_the_large_lines(imac):
    pages = imac[2].pages
    assert page_title(page_of(pages, 3)) == "Taking responsibility for our products at every stage"
    assert page_title(page_of(pages, 10)) == "Carbon Footprint"


def test_unprinted_chart_values_become_approximate_and_unseen_table_numbers_are_reported():
    chart = {"kind": "chart", "title": "", "markdown": "Baseline emissions: 445 kg", "footnote_refs": [],
             "values": [{"label": "Use", "value": "130", "unit": "kg", "estimated": False}]}
    fixed, warnings = verify(chart, {"445"}, 2)
    assert fixed["values"][0]["estimated"] is True
    assert warnings == ["p2: chart value Use=130 not printed; marked approximate"]
    table = {"kind": "table", "title": "", "markdown": "| Sleep | 0.59W | 0.95W |", "values": [], "footnote_refs": []}
    assert verify(table, {"0.59"}, 12)[1] == ["p12: table numbers not in the text layer: 0.95"]


def test_chart_content_lists_values_and_marks_estimates():
    chart = {"kind": "chart", "title": "iMac emissions", "markdown": "iMac emissions: 346 kg", "footnote_refs": [],
             "values": [{"label": "Transportation", "value": "5", "unit": "kg", "estimated": True},
                        {"label": "Total", "value": "346", "unit": "kg", "estimated": False}]}
    assert block_content(chart) == f"iMac emissions\n\niMac emissions: 346 kg\n\nValues: Transportation: 5 kg {APPROXIMATE}; Total: 346 kg"


def test_endnote_table_links_to_the_note_naming_its_rows():
    table = {"kind": "table", "title": "", "values": [], "footnote_refs": [],
             "markdown": "| Mode | 100V |\n|---|---|\n| Off | 0.27W |\n| Sleep | 0.56W |"}
    notes = [Note(17, 12, "Responsible sourcing of wood fiber."), Note(18, 12, "Off: Lowest power mode. Sleep: Low power state.")]
    assert table_notes(table, notes) == [18]


def test_one_section_per_titled_page(imac):
    result = imac[0]
    assert [s.title for s in result.sections if s.level == 1] == [
        "Product Environmental Report", "Progress toward carbon neutral",
        "Taking responsibility for our products at every stage", "Design and Source", "Make",
        "Package and Ship", "Use", "Recover", "Definitions", "Carbon Footprint", "Endnotes",
    ]
    endnotes = next(s for s in result.sections if s.title == "Endnotes")
    assert (endnotes.page_start, endnotes.page_end) == (11, 12)


def test_charts_tables_and_callouts_are_their_own_chunks(imac):
    chunks = imac[0].chunks
    kinds = [c.kind for c in chunks]
    assert (kinds.count("chart"), kinds.count("table"), kinds.count("callout")) == (3, 3, 4)
    carbon = next(c for c in chunks if c.kind == "table" and "Total product footprint" in c.content)
    assert "| Total product footprint | 346 kg CO2e |" in carbon.content and "45%" in carbon.content
    chart = next(c for c in chunks if c.kind == "chart" and "Baseline emissions: 445 kg" in c.content)
    assert APPROXIMATE in chart.content and chart.metadata == {"estimated": True}


def test_footnote_markers_become_refs_not_text(imac):
    chunks = imac[0].chunks
    use = next(c for c in chunks if c.kind == "text" and "58 percent less energy" in c.content)
    assert "requirement for ENERGY STAR." in use.content and "STAR.^18" not in use.content
    assert (7, 18) in use.refs
    assert not [c for c in chunks if any(glued in c.content for glued in ("emissions7", "packaging3", "sourced17", "Apple.7"))]


def test_endnote_pages_keep_only_the_power_table(imac):
    result, by_key, _ = imac
    endnote_chunks = [c for c in result.chunks if by_key[c.section_key].title == "Endnotes"]
    assert [c.kind for c in endnote_chunks] == ["table"]
    assert endnote_chunks[0].refs == ((12, 18),)
    assert "| Sleep | 0.56W | 0.57W | 0.59W |" in endnote_chunks[0].content


def test_vision_failure_falls_back_to_the_text_layer():
    prepared = prepare(DOCS["imac"])
    prepared.describe = described(fail_on={5})
    result = build(DOCS["imac"], prepared)
    assert [w for w in result.warnings if w.startswith("p5: vision failed")]
    assert [c for c in result.chunks if c.kind == "text" and c.page_start == 5 and "Apple Supplier Code of Conduct" in c.content]


def test_fallback_survives_exceptions_without_a_message():
    prepared = prepare(DOCS["imac"])

    def describe(page_number):
        if page_number == 5:
            raise TimeoutError()
        return [clean_block(b) for b in FIXTURE[str(page_number)]]

    prepared.describe = describe
    result = build(DOCS["imac"], prepared)
    assert "p5: vision failed (TimeoutError); used the text layer" in result.warnings


def test_every_text_layer_marker_reaches_a_chunk_on_its_page(imac):
    result, _, prepared = imac
    for page in prepared.pages:
        if page.number in DOCS["imac"].endnote_pages:
            continue
        expected = {ref.n for line in page.lines for ref in line.refs}
        cited = {n for c in result.chunks for p, n in c.refs if p == page.number}
        assert expected <= cited, (page.number, expected - cited)
    claim = next(c for c in result.chunks if c.page_start == 5 and "Zero Waste" in c.content)
    assert (5, 16) in claim.refs

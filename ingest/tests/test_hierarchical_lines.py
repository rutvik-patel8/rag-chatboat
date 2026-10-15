import pytest

from chunk.hierarchical import GEP_LAYOUT, chapter_starts, figure_text, line_role, table_pieces
from helpers import page_of
from model import Line, Span
from profiles import DOCS


def first(page, prefix):
    return next(l for l in page.lines if l.text.startswith(prefix))


@pytest.mark.parametrize(
    "page, prefix, role",
    [
        (19, "Executive Summary", "front"),
        (25, "Summary", "section"),
        (103, "References", "section"),
        (30, "From task-level gains", "subsection"),
        (52, "Downside risks", "minor"),
        (27, "FIGURE 1.1", "object"),
        (27, "Global growth is expected to slow in 2026", "graphic"),
        (19, "The global economy", "text"),
    ],
)
def test_line_roles(gep_prepared, page, prefix, role):
    assert line_role(first(page_of(gep_prepared.pages, page), prefix), GEP_LAYOUT) == role


def test_box_text_is_the_smaller_body_size(gep_prepared):
    line = first(page_of(gep_prepared.pages, 31), "Most studies focus on the United States")
    assert line_role(line, GEP_LAYOUT) == "box_text"


def test_chapter_starts_come_from_bookmarks():
    assert chapter_starts(DOCS["gep"].path) == {
        23: "Ch. 1 Global Outlook",
        69: "Ch. 2 Regional Outlooks",
        107: "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs",
        143: "Ch. 4 Navigating Volatility: Fiscal Policy and Commodity Price Swings",
        185: "Statistical Annex",
    }


def styled(text, font, size):
    return Line((Span(text, size, 4, (0, 0, 1, 1), font, 0),), (0, 0, 1, 1), 0)


def test_figure_text_keeps_takeaway_panels_and_sources_but_not_ticks():
    lines = [
        styled("Growth slows in 2026.", "HelveticaLTStd-Obl", 8.0),
        styled("A. Share of economies by 2026", "HelveticaLTStd-Bold", 7.0),
        styled("forecast revision", "HelveticaLTStd-Bold", 7.0),
        styled("B. Energy prices", "HelveticaLTStd-Bold", 7.0),
        styled("2.5", "Helvetica", 5.7),
        styled("Sources: World Bank.", "HelveticaLTStd-Roman", 6.0),
    ]
    assert figure_text("FIGURE 1.1 Global economic prospects", lines, GEP_LAYOUT) == (
        "FIGURE 1.1 Global economic prospects\n\nGrowth slows in 2026.\n\n"
        "Panels: A. Share of economies by 2026 forecast revision; B. Energy prices\n\nSources: World Bank."
    )


def test_south_asia_forecast_table(gep_prepared):
    page = page_of(gep_prepared.pages, 98)
    top, below = first(page, "TABLE 2.9"), first(page, "TABLE 2.10")
    members = [
        l for l in page.lines if line_role(l, GEP_LAYOUT) == "graphic" and top.bbox[1] - 1 <= l.bbox[1] < below.bbox[1] - 1
    ]
    pieces, notes = table_pieces("TABLE 2.9 South Asia forecast summary", members, page, GEP_LAYOUT)
    assert len(pieces) >= 2
    assert all(p.startswith("TABLE 2.9 South Asia forecast summary\n\n") for p in pieces)
    revision = "(change vs January 2026 projections)"
    assert all(f"|  | 2023 | 2024 | 2025e | 2026f | 2027f | 2028f | 2026f {revision} | 2027f {revision} |" in p for p in pieces)
    assert "| EMDE SAR, GDP | 6.6 | 6.8 | 7.0 | 6.3 | 6.9 | 7.1 | 0.1 | 0.4 |" in pieces[0]
    assert notes.startswith("TABLE 2.9 South Asia forecast summary\n\n")


def test_repeated_year_columns_are_labelled_as_revisions():
    from chunk.hierarchical import label_revision_columns

    rows = [["Percentage-point differences", "from January 2026 projections"], ["", "2025e", "2026f", "2027f", "2026f", "2027f"]]
    assert label_revision_columns(rows, 1)[1] == [
        "", "2025e", "2026f", "2027f",
        "2026f (change vs January 2026 projections)", "2027f (change vs January 2026 projections)",
    ]

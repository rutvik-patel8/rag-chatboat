from chunk.narrative import (
    GPO_LAYOUT,
    Heading,
    Style,
    financial_heading,
    hoist_page_title,
    is_caption,
    merge_drop_caps,
    narrative_heading,
    repeated_titles,
)
from helpers import line_with, make_line, make_page, page_of
from model import Span


def test_style_matches_font_size_and_colour():
    style = Style("HelveticaNeueLTStd-Md", 9.5, 0x507CBF)
    assert style.matches(Span("govinfo", 9.52, 4, (0, 0, 1, 1), "HelveticaNeueLTStd-Md", 0x507CBF))
    assert not style.matches(Span("govinfo", 9.52, 4, (0, 0, 1, 1), "HelveticaNeueLTStd-Md", 0x404041))


def test_grey_title_is_level_one(gpo_prepared):
    line = line_with(page_of(gpo_prepared.pages, 11), "GPO and Congress")
    assert narrative_heading(line, GPO_LAYOUT) == Heading(1, "GPO and Congress")


def test_run_in_heading_keeps_the_rest_of_the_line_as_body(gpo_prepared):
    heading = narrative_heading(line_with(page_of(gpo_prepared.pages, 17), "Partnership with Industry"), GPO_LAYOUT)
    assert (heading.level, heading.title) == (2, "Partnership with Industry")
    assert heading.rest.text.startswith("Other than")


def test_dark_grey_emphasis_is_not_a_heading(gpo_prepared):
    line = line_with(page_of(gpo_prepared.pages, 8), "Facebook facebook.com/USGPO")
    assert narrative_heading(line, GPO_LAYOUT) is None


def test_photo_caption_style(gpo_prepared):
    page = page_of(gpo_prepared.pages, 10)
    assert is_caption(line_with(page, "(Above) GPO employees producing the Congressional Record."), GPO_LAYOUT)
    assert not is_caption(line_with(page, "System, or FDsys. FDsys is scheduled"), GPO_LAYOUT)


def test_financial_headings_skip_table_labels_and_repeated_titles(gpo_prepared):
    repeated = repeated_titles(gpo_prepared.pages, GPO_LAYOUT)
    assert repeated == {"U.S. Government Publishing Office"}
    p35, p58, p69 = (page_of(gpo_prepared.pages, n) for n in (35, 58, 69))
    assert financial_heading(next(l for l in p35.lines if l.text == "Mission"), p35, repeated) == Heading(2, "Mission")
    assert financial_heading(next(l for l in p69.lines if l.text == "Unrestricted"), p69, repeated) is None
    title = next(l for l in p58.lines if l.text == "U.S. Government Publishing Office")
    assert financial_heading(title, p58, repeated) is None


def test_divider_page_words_are_level_one(gpo_prepared):
    p34 = page_of(gpo_prepared.pages, 34)
    assert financial_heading(next(l for l in p34.lines if l.text == "MANAGEMENT"), p34, set()) == Heading(1, "MANAGEMENT")


def test_drop_cap_rejoins_its_word(gpo_prepared):
    page = merge_drop_caps(page_of(gpo_prepared.pages, 7), gpo_prepared.body_size)
    assert any(l.text.startswith("The U.S. Government Publishing Office (GPO),") for l in page.lines)
    assert not any(l.text == "T" for l in page.lines)


def test_page_top_title_moves_before_body_listed_first():
    page = make_page(4, [make_line("Body below the title", y0=100), make_line("Title", y0=50), make_line("Lower title", y0=300)])
    moved = hoist_page_title(page, lambda l: l.text in {"Title", "Lower title"})
    assert [l.text for l in moved.lines] == ["Title", "Body below the title", "Lower title"]

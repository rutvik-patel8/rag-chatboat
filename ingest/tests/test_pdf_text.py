import pytest

from helpers import line_with
from model import Line, Span
from parse.pdf_text import body_font_size


def test_line_text_collapses_whitespace():
    line = Line((Span("  iMac\tuses ", 8.5, 4, (0, 0, 1, 1)), Span("58  percent ", 8.5, 4, (1, 0, 2, 1))), (0, 0, 2, 1), 0)
    assert line.text == "iMac uses 58 percent"


def test_line_size_is_the_longest_span_not_the_largest():
    bullet = Span("•", 8.4, 4, (0, 0, 1, 1))
    words = Span(" Sleep: Low power state", 7.0, 4, (1, 0, 9, 1))
    assert Line((bullet, words), (0, 0, 9, 1), 0).size == 7.0


def test_superscript_flag_is_bit_zero():
    assert Span("18", 5.7, 5, (0, 0, 1, 1)).is_superscript
    assert not Span("18", 5.7, 4, (0, 0, 1, 1)).is_superscript


def test_pages_are_numbered_from_one(imac_pages):
    assert [p.number for p in imac_pages] == list(range(1, 13))


def test_ligatures_and_nbsp_are_normalised(gep_pages, imac_pages):
    assert "affected by the conflict" in line_with(gep_pages[42], "by the conflict").text
    assert not any("ﬀ" in line.text or "ﬂ" in line.text for page in gep_pages for line in page.lines)
    assert "58 percent less energy" in line_with(imac_pages[6], "iMac consumes").text


@pytest.mark.parametrize("fixture, expected", [("gpo_pages", 12.0), ("gep_pages", 11.0), ("imac_pages", 8.5)])
def test_body_font_size(request, fixture, expected):
    assert body_font_size(request.getfixturevalue(fixture)) == expected


def test_superscript_marker_survives_loading(imac_pages):
    line = line_with(imac_pages[6], "iMac consumes")
    assert [s.text for s in line.spans if s.is_superscript] == ["18"]


def test_th_ligature_glyph_is_repaired(gep_pages):
    words = [w for page in gep_pages for line in page.lines for w in line.text.split()]
    assert "These" in words and "Third," in words
    assert not {"Tese", "Tey", "Teir", "Tere", "Tird"} & set(words)

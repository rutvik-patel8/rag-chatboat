from helpers import line_with, make_line, make_page
from model import Line, Note, Ref, Span
from parse.footnotes import NoteIndex, note_number, split_notes, strip_markers
from parse.pdf_text import body_font_size


def two_span_line(number, number_size, text, text_size):
    return Line(
        (Span(number, number_size, 4, (209, 98, 213, 105)), Span(text, text_size, 4, (217, 98, 400, 106))),
        (209, 98, 400, 106),
        0,
    )


def test_note_number_needs_a_small_number_before_larger_text():
    assert note_number(two_span_line("18", 4.7, "Energy consumption values", 7.0)) == 18
    assert note_number(two_span_line("2023", 4.7, "was a year", 7.0)) is None  # 4 digits
    assert note_number(two_span_line("18", 7.0, "Energy consumption", 7.0)) is None  # same size
    assert note_number(make_line("18 Energy consumption values")) is None  # single span


def notes_of(pages, numbers):
    body = body_font_size(pages)
    found = []
    for n in numbers:
        found += split_notes(pages[n - 1], body)[0]
    return found


def test_imac_endnotes_are_all_found(imac_pages):
    notes = notes_of(imac_pages, [11, 12])
    assert [n.n for n in notes] == list(range(1, 20))
    assert notes[0].text.startswith("Product recycled or renewable content is the mass")
    note18 = next(n for n in notes if n.n == 18)
    assert note18.page == 12
    assert "ENERGY STAR Program Requirements" in note18.text
    assert "Power adapter efficiency" in note18.text  # bullet lines continue the note


def test_gep_page_footnote_is_joined_across_lines(gep_pages):
    [note] = notes_of(gep_pages, [41])
    assert note == Note(1, 41, "Debt refers to aggregate non-financial sector debt as measured by total social financing (excluding equity).")


def test_gep_has_32_page_footnotes(gep_pages):
    assert len(notes_of(gep_pages, range(1, 201))) == 32


def test_split_notes_removes_note_lines_from_the_page(imac_pages):
    _, rest = split_notes(imac_pages[10], body_font_size(imac_pages))
    assert not any(l.text.startswith("1 Product recycled") for l in rest.lines)


def test_imac_marker_is_removed_and_recorded(imac_pages):
    page = strip_markers(split_notes(imac_pages[6], 8.5)[1])
    line = line_with(page, "iMac consumes")
    assert line.text == "on the market. iMac consumes 58 percent less energy than the requirement for ENERGY STAR."
    assert line.refs == (Ref(7, 18, len(line.text)),)


def test_gep_marker_is_removed_and_recorded(gep_pages):
    page = strip_markers(split_notes(gep_pages[40], 11.0)[1])
    line = line_with(page, "potential growth.")
    assert line.text == "potential growth. As exports ease, medium-term"
    assert line.refs == (Ref(41, 1, len("potential growth.")),)


def test_marker_lists_and_non_digit_superscripts():
    body = Span("Recycled cobalt", 8.5, 4, (0, 0, 60, 10))
    listed = Span("12,13", 5.0, 5, (60, 0, 70, 8))
    trademark = Span("TM", 5.0, 5, (70, 0, 80, 8))
    page = make_page(4, [Line((body, listed, trademark), (0, 0, 80, 10), 0)])
    [line] = strip_markers(page).lines
    assert line.refs == (Ref(4, 12, 15), Ref(4, 13, 15))
    assert line.text == "Recycled cobaltTM"


def test_resolve_same_page_first_then_endnote_pages():
    notes = [Note(1, 41, "chapter 1 note"), Note(1, 110, "chapter 3 note"), Note(7, 11, "endnote 7")]
    index = NoteIndex(notes, endnote_pages=frozenset({11, 12}))
    assert index.resolve(1, 110).text == "chapter 3 note"
    assert index.resolve(7, 2).text == "endnote 7"


def test_unmatched_marker_is_unresolved_not_guessed():
    index = NoteIndex([Note(2, 43, "an unrelated page footnote")])
    assert index.resolve(2, 26) is None  # e.g. a GEP table-note marker on p26
    assert index.resolve(9, 43) is None


def test_note_lines_keep_compound_hyphens(gep_pages):
    import re

    texts = [n.text for n in notes_of(gep_pages, range(1, 201))]
    assert not [t for t in texts if re.search(r"[a-z]- [a-z]", t)]


def test_space_separated_marker_lists_are_stripped(gep_pages):
    page = strip_markers(gep_pages[186])
    line = next(l for l in page.lines if l.text.startswith("Myanmar"))
    assert line.text == "Myanmar"
    assert [r.n for r in line.refs] == [3, 4]

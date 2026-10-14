from chunk.common import Paragraph, build_embed_text, count_tokens, pack, paragraphs_from_pages, split_sentences
from helpers import make_line, make_page
from model import Ref
from parse.boilerplate import find_boilerplate, strip_boilerplate


def words(text):
    return len(text.split())


def test_count_tokens_uses_cl100k():
    assert count_tokens("hello world") == 2


def test_split_sentences():
    assert split_sentences("GPO grew. It printed 5 titles! Done? e.g. lower case stays") == [
        "GPO grew.",
        "It printed 5 titles!",
        "Done? e.g. lower case stays",
    ]


def test_lines_in_one_block_join_and_hyphenation_is_repaired():
    page = make_page(3, [make_line("Caps on prices limit pass-through to house-", y0=100), make_line("holds this year.", y0=112)])
    assert paragraphs_from_pages([page], frozenset({"households"})) == [
        Paragraph("Caps on prices limit pass-through to households this year.", 3)
    ]


def test_compound_words_keep_their_hyphen_across_lines():
    page = make_page(3, [make_line("most middle-", y0=100), make_line("income countries", y0=112)])
    assert paragraphs_from_pages([page], frozenset()) == [Paragraph("most middle-income countries", 3)]


def test_gep_compound_words_survive(gep_pages):
    pages = strip_boilerplate(gep_pages, find_boilerplate(gep_pages))
    text = " ".join(p.text for p in paragraphs_from_pages(pages)).lower()
    assert "middle-income" in text and "middleincome" not in text


def test_block_change_size_change_and_gap_start_new_paragraphs():
    page = make_page(
        1,
        [
            make_line("First block", y0=100, block=0),
            make_line("Second block", y0=112, block=1),
            make_line("Bigger heading", y0=124, block=1, size=14.0),
            make_line("Far below", y0=300, block=1, size=14.0),
        ],
    )
    assert [p.text for p in paragraphs_from_pages([page])] == ["First block", "Second block", "Bigger heading", "Far below"]


def test_paragraph_refs_are_unioned_in_order():
    first = make_line("iMac uses 58 percent less", y0=100, refs=(Ref(7, 18, 25),))
    second = make_line("energy.", y0=112, refs=(Ref(7, 3, 7),))
    [paragraph] = paragraphs_from_pages([make_page(7, [first, second])])
    assert paragraph.text == "iMac uses 58 percent less energy."
    assert paragraph.refs == (Ref(7, 18, 25), Ref(7, 3, 33))


def test_gpo_fact_stays_in_one_paragraph(gpo_pages):
    pages = strip_boilerplate(gpo_pages, find_boilerplate(gpo_pages))
    texts = [p.text for p in paragraphs_from_pages(pages)]
    assert any("GPO staff visited 166 depository libraries in 25 states" in t for t in texts)


ONE, TWO, THREE = "one one one one", "two two two two", "three three three three"


def test_short_paragraphs_are_packed_up_to_the_target():
    chunks = pack([Paragraph(ONE, 1), Paragraph(TWO, 2), Paragraph(THREE, 3)], 8, 0, count=words)
    assert [(c.text, c.page_start, c.page_end) for c in chunks] == [(f"{ONE}\n\n{TWO}", 1, 2), (THREE, 3, 3)]


def test_overlap_repeats_the_trailing_paragraph():
    chunks = pack([Paragraph(ONE, 1), Paragraph(TWO, 2), Paragraph(THREE, 3)], 8, 4, count=words)
    assert [c.text for c in chunks] == [f"{ONE}\n\n{TWO}", f"{TWO}\n\n{THREE}"]


def test_overlap_is_dropped_when_it_would_overflow():
    long = "seven words in this one long paragraph"
    chunks = pack([Paragraph(ONE, 1), Paragraph(long, 2)], 8, 4, count=words)
    assert [c.text for c in chunks] == [ONE, long]


def test_long_paragraph_splits_on_sentences():
    text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."
    assert [c.text for c in pack([Paragraph(text, 1)], 4, 0, count=words)] == [
        "Alpha beta gamma.",
        "Delta epsilon zeta.",
        "Eta theta iota.",
    ]


def test_sentence_without_breaks_is_hard_split():
    text = " ".join(f"w{i}" for i in range(10))
    assert [words(c.text) for c in pack([Paragraph(text, 1)], 4, 0, count=words)] == [4, 4, 2]


def test_chunk_refs_union_paragraph_refs():
    chunks = pack([Paragraph(ONE, 1, (Ref(1, 18, 15),)), Paragraph(TWO, 1, (Ref(1, 7, 15), Ref(1, 18, 4)))], 8, 0, count=words)
    assert chunks[0].refs == ((1, 18), (1, 7))


def test_refs_go_only_to_the_sentence_holding_the_marker():
    text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."
    marker = Ref(1, 5, len("Alpha beta gamma. Delta epsilon zeta."))
    chunks = pack([Paragraph(text, 1, (marker,))], 4, 0, count=words)
    assert [c.refs for c in chunks] == [(), ((1, 5),), ()]


def test_refs_keep_their_own_page_when_a_chunk_spans_pages():
    chunks = pack([Paragraph(ONE, 42), Paragraph(TWO, 43, (Ref(43, 2, 15),))], 8, 0, count=words)
    assert [(c.page_start, c.refs) for c in chunks] == [(42, ((43, 2),))]


def test_embed_text_header_with_section_and_page_range():
    text = build_embed_text("GEP June 2026", "Ch. 2 › South Asia", 95, 96, "text", "Growth is projected")
    assert text == "GEP June 2026 › Ch. 2 › South Asia | p.95-96 | text\n\nGrowth is projected"


def test_embed_text_header_without_section_path():
    assert build_embed_text("iMac", "", 7, 7, "footnote", "x") == "iMac | p.7 | footnote\n\nx"

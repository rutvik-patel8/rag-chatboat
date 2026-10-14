from collections import Counter

import pytest

from helpers import make_line, make_page
from model import ChunkDraft, Note, SectionDraft
from parse.footnotes import NoteIndex
from pipeline import DocSpec, Prepared, attach_footnotes, check_token_limit, note_chunks, section_for_page
from chunk.plain import build
from report import Report

SECTIONS = [
    SectionDraft("root", "Doc", 0, "", 1, 200),
    SectionDraft("ch2", "Ch. 2", 1, "Ch. 2", 69, 106, parent_key="root"),
    SectionDraft("ch2/sar", "South Asia", 2, "Ch. 2 › South Asia", 95, 98, parent_key="ch2"),
]


def test_section_for_page_picks_the_deepest_match():
    assert section_for_page(SECTIONS, 96).key == "ch2/sar"
    assert section_for_page(SECTIONS, 70).key == "ch2"
    assert section_for_page(SECTIONS, 5).key == "root"


def test_note_chunks_are_footnote_kind_in_the_notes_section():
    [chunk] = note_chunks([Note(1, 96, "Fiscal years.")], SECTIONS)
    assert (chunk.section_key, chunk.kind, chunk.content, chunk.page_start) == ("ch2/sar", "footnote", "Footnote 1: Fiscal years.", 96)


def test_attach_footnotes_resolves_and_reports_unresolved():
    chunks = [ChunkDraft("root", "text", "58 percent less energy", 7, 7, refs=((7, 18), (7, 4)))]
    report = Report("imac", "iMac")
    attach_footnotes(chunks, NoteIndex([Note(18, 12, "ENERGY STAR rules.")], frozenset({12})), report)
    assert chunks[0].footnotes == [{"n": 18, "text": "ENERGY STAR rules."}]
    assert report.unresolved == [(7, 4)]


def test_token_limit_fails_before_embedding():
    chunk = ChunkDraft("root", "table", "x", 186, 186)
    with pytest.raises(ValueError, match="p.186"):
        check_token_limit(["word " * 2100], [chunk])
    check_token_limit(["short"], [chunk])  # no error


def test_report_format_lists_counts_and_unresolved():
    report = Report("imac", "iMac report")
    report.pages, report.sections, report.notes = 12, 1, 19
    report.chunks_by_kind = Counter({"text": 15, "footnote": 19})
    report.unresolved = [(3, 12)]
    text = report.format()
    assert "imac — iMac report" in text
    assert "footnote 19, text 15 (total 34)" in text
    assert "p3:12" in text


def test_plain_profile_handles_empty_pages_and_no_notes(tmp_path):
    pages = [
        make_page(1, []),  # photo-only page
        make_page(2, [make_line("GPO distributed 802,834 copies of 4,211 titles.")]),
        make_page(3, [make_line("Second page text.")]),
    ]
    spec = DocSpec("gpo", "GPO 2018 Annual Report", tmp_path / "x.pdf", "plain")
    result = build(spec, Prepared(pages, 10.0, NoteIndex([]), 3, 0))
    [root] = result.sections
    assert (root.key, root.level, root.page_start, root.page_end) == ("root", 0, 1, 3)
    assert [c.content for c in result.chunks] == ["GPO distributed 802,834 copies of 4,211 titles.\n\nSecond page text."]


def test_refs_resolve_on_their_own_page_and_report_each_marker_once():
    index = NoteIndex([Note(2, 43, "Oil-exporting economies.")])
    chunks = [
        ChunkDraft("root", "text", "spans p42-43", 42, 43, refs=((43, 2), (43, 2))),
        ChunkDraft("root", "text", "overlap tail", 43, 43, refs=((43, 2), (26, 2))),
        ChunkDraft("root", "table", "table", 26, 26, refs=((26, 2),)),
    ]
    report = Report("gep", "GEP")
    attach_footnotes(chunks, index, report)
    assert chunks[0].footnotes == [{"n": 2, "text": "Oil-exporting economies."}]
    assert report.unresolved == [(26, 2)]


def test_every_gep_page_footnote_reaches_a_chunk():
    from pipeline import prepare
    from profiles import DOCS

    prepared = prepare(DOCS["gep"])
    result = build(DOCS["gep"], prepared)
    attach_footnotes(result.chunks, prepared.notes, Report("gep", "GEP"))
    cited = {(f["n"], f["text"]) for c in result.chunks for f in c.footnotes}
    assert len(prepared.notes.notes) == 32
    assert cited == {(n.n, n.text) for n in prepared.notes.notes}


def test_prepare_keeps_raw_pages_for_profiles():
    from pipeline import prepare
    from profiles import DOCS

    prepared = prepare(DOCS["imac"])
    raw7 = next(p for p in prepared.raw_pages if p.number == 7)
    assert any(s.text == "18" and s.is_superscript for l in raw7.lines for s in l.spans)
    raw11 = next(p for p in prepared.raw_pages if p.number == 11)
    assert any(l.text.startswith("1 Product recycled") for l in raw11.lines)
    assert not any("iMac | Product Environmental Report" in l.text for p in prepared.raw_pages for l in p.lines)


def test_note_chunk_goes_to_the_section_that_cites_it():
    note = Note(1, 96, "Fiscal years.")
    [cited] = note_chunks([note], SECTIONS, cited={note: "ch2"})
    [uncited] = note_chunks([Note(2, 96, "Other.")], SECTIONS, cited={note: "ch2"})
    assert (cited.section_key, uncited.section_key) == ("ch2", "ch2/sar")

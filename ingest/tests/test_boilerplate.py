from helpers import make_line, make_page
from parse.boilerplate import find_boilerplate, margin_key, strip_boilerplate


def test_margin_key_ignores_letter_spacing_case_and_numbers():
    spaced = "U . S . G O V E R N M E N T  P U B L I S H I N G  O F F I C E | 2 0 1 8  A N N U A L  R E P O R T"
    assert margin_key(spaced) == "u.s.governmentpublishingoffice|#annualreport"
    assert margin_key("CHAPTER 3") == margin_key("Chapter 1") == "chapter#"


def header_pages(total, with_header):
    pages = []
    for n in range(1, total + 1):
        lines = [make_line("Body text on this page", y0=400)]
        if n <= with_header:
            lines.append(make_line("Running Header", y0=20))
        pages.append(make_page(n, lines))
    return pages


def test_header_on_too_few_pages_is_kept():
    assert find_boilerplate(header_pages(10, 2)) == set()


def test_header_on_min_pages_is_found_and_only_margin_lines_are_removed():
    pages = header_pages(10, 3)
    pages.append(make_page(11, [make_line("Running Header", y0=400)]))  # same words, mid-page: real content
    keys = find_boilerplate(pages)
    assert keys == {"runningheader"}
    stripped = strip_boilerplate(pages, keys)
    assert [l.text for l in stripped[0].lines] == ["Body text on this page"]
    assert [l.text for l in stripped[10].lines] == ["Running Header"]


def test_gep_running_headers_are_removed(gep_pages):
    keys = find_boilerplate(gep_pages)
    assert {"globaleconomicprospects|june#", "chapter#", "#"} <= keys
    stripped = strip_boilerplate(gep_pages, keys)
    texts = [l.text for l in stripped[51].lines]
    assert "GLOBAL ECONOMIC PROSPECTS | JUNE 2026" not in texts
    assert any("natural gas production and exports led to a" in t for t in (l.text for l in stripped[40].lines))


def test_gpo_alternating_letter_spaced_headers_are_found(gpo_pages):
    keys = find_boilerplate(gpo_pages)
    assert "u.s.governmentpublishingoffice|#annualreport" in keys
    assert "#annualreport|u.s.governmentpublishingoffice" in keys


def test_imac_footer_is_found(imac_pages):
    assert "imac|productenvironmentalreport" in find_boilerplate(imac_pages)

from helpers import make_line, make_page
from parse.tables import region_rows, remove_region, rows_to_markdown, split_table


def first_line(page, exact):
    return next(l for l in page.lines if l.text == exact)


def test_imac_power_table_rows(imac_pages):
    page = imac_pages[11]
    top = first_line(page, "Power consumption for iMac")
    bottom = first_line(page, "Power adapter efficiency")
    rows = region_rows(page, (0, top.bbox[1] - 1, page.width, bottom.bbox[3] + 1))
    assert rows == [
        ["Power consumption for iMac"],
        ["Mode", "100V", "115V", "230V"],
        ["Off", "0.27W", "0.27W", "0.29W"],
        ["Sleep", "0.56W", "0.57W", "0.59W"],
        ["Idle—Display On", "20.5W", "20.6W", "20.9W"],
        ["Power adapter, no load", "0.10W", "0.10W", "0.12W"],
        ["Power adapter efficiency", "91.1%", "91.5%", "91.9%"],
    ]


def test_gep_table_1_1_rows(gep_pages):
    page = gep_pages[25]
    top = next(l for l in page.lines if l.text.startswith("TABLE 1.1"))
    rows = region_rows(page, (0, top.bbox[1] - 1, page.width, page.height * 0.92))
    assert rows[0][0].startswith("TABLE 1.1")
    assert ["2023", "2024", "2025e", "2026f", "2027f", "2028f", "2026f", "2027f"] in rows
    assert ["World", "2.8", "2.9", "2.9", "2.5", "2.8", "2.8", "-0.1", "0.1"] in rows


def test_rows_to_markdown_pads_short_rows_and_escapes_pipes():
    assert rows_to_markdown([["Mode", "a|b"], ["Off"]]) == "| Mode | a\\|b |\n|---|---|\n| Off |  |"


def lines_count(text):
    return text.count("\n") + 1


def test_split_table_repeats_the_header_in_every_piece():
    rows = [["Mode", "230V"]] + [[f"row{i}", "1W"] for i in range(5)]
    pieces = split_table(rows, header_rows=1, max_tokens=4, count=lines_count)
    assert len(pieces) == 3
    assert all(piece.startswith("| Mode | 230V |\n|---|---|") for piece in pieces)
    assert "row4" in pieces[-1]


def test_split_table_never_splits_a_row_even_when_it_is_too_big():
    rows = [["Mode", "230V"]] + [[f"row{i}", "1W"] for i in range(3)]
    assert len(split_table(rows, header_rows=1, max_tokens=1, count=lines_count)) == 3


def test_remove_region_drops_only_lines_inside_the_box():
    page = make_page(1, [make_line("caption", y0=90), make_line("cell", y0=200), make_line("after", y0=400)])
    assert [l.text for l in remove_region(page, (0, 150, 600, 300)).lines] == ["caption", "after"]


def test_lone_dollar_cells_join_the_value_after_them():
    from parse.tables import merge_symbol_cells

    rows = [["Fund balance with Treasury (Note 2)", "$", "644,807", "$", "585,883"], ["Inventory", "57,369"]]
    assert merge_symbol_cells(rows) == [["Fund balance with Treasury (Note 2)", "$644,807", "$585,883"], ["Inventory", "57,369"]]

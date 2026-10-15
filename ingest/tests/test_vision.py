from enrich.vision import PageDescriber, clean_block, render_page, strip_markers
from cache import JsonCache
from profiles import DOCS


def block(kind="text", markdown="", title="", values=(), refs=()):
    return {"kind": kind, "title": title, "markdown": markdown, "values": list(values), "footnote_refs": list(refs)}


def test_superscript_markers_become_refs():
    cleaned = clean_block(block(markdown="GHG reductions achieved⁷ ↓>20%"))
    assert cleaned["markdown"] == "GHG reductions achieved ↓>20%"
    assert cleaned["footnote_refs"] == [7]


def test_listed_refs_glued_to_words_are_removed_but_values_stay():
    cleaned = clean_block(block(markdown="ENERGY STAR.^18 Baseline emissions7: 445 kg, 0.59W, CO₂e", refs=[7, 18]))
    assert cleaned["markdown"] == "ENERGY STAR. Baseline emissions: 445 kg, 0.59W, CO2e"
    assert strip_markers("packaging3 is 100%", {3}) == "packaging is 100%"


def test_tables_rejoin_wrapped_rows_and_headings_are_not_repeated():
    table = clean_block(block("table", "| Greenhouse gas emissions | iMac (two ports)\n256GB |\n| --- | --- |"))
    assert table["markdown"] == "| Greenhouse gas emissions | iMac (two ports) 256GB |\n| --- | --- |"
    text = clean_block(block(title="Use", markdown="# Use\n\n**Designed** to last."))
    assert (text["title"], text["markdown"]) == ("", "# Use\n\nDesigned to last.")


def test_render_page_returns_a_png():
    assert render_page(DOCS["imac"].path, 2)[:4] == b"\x89PNG"


class FakeVision:
    def __init__(self):
        self.calls = 0

    def generate_json(self, prompt, schema, image_png):
        self.calls += 1
        assert image_png[:4] == b"\x89PNG" and "footnote" in prompt and schema["required"] == ["blocks"]
        return {"blocks": [block("callout", "100% of the packaging³ is fiber-based")]}


def test_describer_caches_by_page_image(tmp_path):
    vision = FakeVision()
    describer = PageDescriber(vision, JsonCache(tmp_path, "imac/vision"), "gemini-3.5-flash-lite")
    first = describer(DOCS["imac"].path, 6)
    again = describer(DOCS["imac"].path, 6)
    assert vision.calls == 1
    assert first == again == [
        {"kind": "callout", "title": "", "markdown": "100% of the packaging is fiber-based", "values": [], "footnote_refs": [3]}
    ]

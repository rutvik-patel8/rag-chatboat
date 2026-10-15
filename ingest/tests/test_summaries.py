from cache import JsonCache
from chunk.common import count_tokens, truncate_tokens
from enrich.summaries import summary_chunks, subtree_texts
from model import SectionDraft

LONG = "Growth in South Asia is projected to slow. " * 60


def tree():
    return [
        SectionDraft("root", "GEP", 0, "", 1, 200),
        SectionDraft("ch2", "Ch. 2 Regional Outlooks", 1, "Ch. 2 Regional Outlooks", 69, 106, "root"),
        SectionDraft("sar", "South Asia", 2, "Ch. 2 Regional Outlooks › South Asia", 96, 98, "ch2", "region"),
        SectionDraft("out", "Outlook", 3, "Ch. 2 Regional Outlooks › South Asia › Outlook", 96, 97, "sar", content=LONG),
        SectionDraft("eap", "East Asia and Pacific", 2, "Ch. 2 Regional Outlooks › East Asia and Pacific", 81, 83, "ch2", "region", "Short text."),
    ]


class FakeGenerator:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return f"summary {len(self.prompts)}"


def test_subtree_text_includes_descendants_in_order():
    texts = subtree_texts(tree())
    assert texts["sar"] == LONG
    assert texts["ch2"] == f"{LONG}\n\nShort text."


def test_only_long_level_one_and_two_sections_are_summarized(tmp_path):
    generator = FakeGenerator()
    chunks = summary_chunks("GEP June 2026", tree(), generator, JsonCache(tmp_path, "gep/summaries"), "m")
    assert [(c.section_key, c.kind, c.page_start, c.page_end) for c in chunks] == [
        ("ch2", "section_summary", 69, 106),
        ("sar", "section_summary", 96, 98),
    ]
    assert chunks[1].content == "Summary of Ch. 2 Regional Outlooks › South Asia: summary 2"


def test_prompt_names_the_report_and_section_and_forbids_outside_facts(tmp_path):
    generator = FakeGenerator()
    summary_chunks("GEP June 2026", tree(), generator, JsonCache(tmp_path, "gep/summaries"), "m")
    prompt = generator.prompts[1]
    assert '"GEP June 2026"' in prompt
    assert "Section: Ch. 2 Regional Outlooks › South Asia" in prompt
    assert "using only facts stated in the text" in prompt


def test_cached_summaries_cost_nothing_on_rerun(tmp_path):
    cache = JsonCache(tmp_path, "gep/summaries")
    summary_chunks("GEP", tree(), FakeGenerator(), cache, "m")
    again = FakeGenerator()
    summary_chunks("GEP", tree(), again, cache, "m")
    assert again.prompts == []


def test_truncate_tokens():
    text = "word " * 50
    assert count_tokens(truncate_tokens(text, 10)) <= 10
    assert truncate_tokens("short text", 10) == "short text"

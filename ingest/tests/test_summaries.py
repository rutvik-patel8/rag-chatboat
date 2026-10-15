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


class QuotaExhausted:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED daily quota")


def test_generation_failure_keeps_cached_summaries_and_stops_calling(tmp_path):
    cache = JsonCache(tmp_path, "gep/summaries")
    sections = tree()
    summary_chunks("GEP", sections[:4], FakeGenerator(), cache, "m")  # caches ch2 (its subtree is only South Asia then)
    sections.append(SectionDraft("ssa", "Sub-Saharan Africa", 2, "Ch. 2 Regional Outlooks › Sub-Saharan Africa", 99, 102, "ch2", "region", LONG))
    failing, warnings = QuotaExhausted(), []
    chunks = summary_chunks("GEP", sections[:4], failing, cache, "m", warnings)
    assert [c.section_key for c in chunks] == ["ch2", "sar"]  # both cached by the first run
    assert failing.calls == 0
    chunks = summary_chunks("GEP", sections, failing, cache, "m", warnings)
    assert failing.calls == 1  # ch2's prompt changed (new child), so it fails; ssa is not even tried
    assert [c.section_key for c in chunks] == ["sar"]
    assert warnings == ["2 section summaries skipped (429 RESOURCE_EXHAUSTED daily quota); re-run ingest.py later to fill them in"]


class BlocksFirstPrompt:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        if self.calls == 1:
            raise ValueError("response blocked by safety filters")
        return "Growth slows."


def test_a_non_quota_failure_skips_only_that_section(tmp_path):
    warnings = []
    chunks = summary_chunks("GEP", tree(), BlocksFirstPrompt(), JsonCache(tmp_path, "gep/summaries"), "m", warnings)
    assert [c.section_key for c in chunks] == ["sar"]
    assert warnings == ["1 section summaries skipped (response blocked by safety filters); re-run ingest.py later to fill them in"]


def test_prompt_forbids_preambles_and_bullets(tmp_path):
    generator = FakeGenerator()
    summary_chunks("GEP", tree(), generator, JsonCache(tmp_path, "gep/summaries"), "m")
    assert "No preamble" in generator.prompts[0] and "no bullet points" in generator.prompts[0]


def test_preambles_and_bullets_are_cleaned_up():
    from enrich.summaries import clean_summary

    assert clean_summary("Here is a 4-sentence summary:\n\nDebt rose to 70 percent.") == "Debt rose to 70 percent."
    assert clean_summary("Based on the provided text, debt rose.") == "Debt rose."
    assert clean_summary("* Debt rose.\n* Spreads widened.") == "Debt rose. Spreads widened."

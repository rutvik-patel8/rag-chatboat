"""The three source documents and the profile each one uses (sub-projects 3–5 swap in real profiles)."""
from __future__ import annotations

from chunk import narrative, plain
from config import REPO_ROOT
from pipeline import DocSpec, Profile

DOCS_DIR = REPO_ROOT / "docs"

DOCS: dict[str, DocSpec] = {
    "gpo": DocSpec(
        "gpo",
        "GPO 2018 Annual Report",
        DOCS_DIR / "GPO-Anual-report.pdf",
        "narrative",
        skip_pages=frozenset({1, 31, 32, 33}),  # covers and the statements' contents page
    ),
    "gep": DocSpec("gep", "Global Economic Prospects, June 2026", DOCS_DIR / "GEP-Jun-2026.pdf", "plain"),
    "imac": DocSpec(
        "imac",
        "iMac Product Environmental Report (Oct 2024)",
        DOCS_DIR / "iMac_PER_Oct2024.pdf",
        "plain",
        endnote_pages=frozenset({11, 12}),
    ),
}

PROFILES: dict[str, Profile] = {"plain": plain.build, "narrative": narrative.build}

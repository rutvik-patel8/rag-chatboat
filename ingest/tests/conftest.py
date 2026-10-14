import pytest

from config import REPO_ROOT
from parse.pdf_text import load_pages

DOCS = REPO_ROOT / "docs"


@pytest.fixture(scope="session")
def imac_pages():
    return load_pages(DOCS / "iMac_PER_Oct2024.pdf")


@pytest.fixture(scope="session")
def gep_pages():
    return load_pages(DOCS / "GEP-Jun-2026.pdf")


@pytest.fixture(scope="session")
def gpo_pages():
    return load_pages(DOCS / "GPO-Anual-report.pdf")


@pytest.fixture(scope="session")
def gpo_prepared():
    from pipeline import prepare
    from profiles import DOCS

    return prepare(DOCS["gpo"])

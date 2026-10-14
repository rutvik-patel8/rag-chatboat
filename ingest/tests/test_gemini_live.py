import math

import pytest

from config import load_settings
from gemini import make_embedder

pytestmark = pytest.mark.live


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))  # inputs are already unit length


def test_real_embeddings_are_normalised_and_meaningful():
    settings = load_settings()
    embedder = make_embedder(settings)
    doc, unrelated = embedder.embed(
        ["GPO distributed 802,834 copies of 4,211 titles.", "The iMac stand is made of recycled aluminum."],
        "RETRIEVAL_DOCUMENT",
    )
    [query] = embedder.embed(["How many copies did GPO distribute?"], "RETRIEVAL_QUERY")
    assert len(doc) == settings.embed_dim
    assert math.isclose(sum(v * v for v in doc), 1.0, rel_tol=1e-6)
    assert cosine(query, doc) > cosine(query, unrelated)

from contextlib import nullcontext

from config import Settings
from doctor import check_database, check_gemini

SETTINGS = Settings(
    gemini_api_key="k",
    database_url="postgresql://rag:rag@localhost:5433/rag",
    chat_model="gemini-3.8-flash",
    embed_model="gemini-embedding-001",
    embed_dim=768,
)


def fake_connect(_settings):
    return nullcontext(object())


def test_database_ok():
    check = check_database(SETTINGS, connect_fn=fake_connect, dim_fn=lambda _c: 768)
    assert check.ok
    assert "vector(768)" in check.detail


def test_database_unreachable_is_reported_not_raised():
    def refuse(_settings):
        raise OSError("connection refused")

    check = check_database(SETTINGS, connect_fn=refuse)
    assert not check.ok
    assert check.detail == "Database check failed: connection refused"


def test_dimension_mismatch_points_to_reset():
    check = check_database(SETTINGS, connect_fn=fake_connect, dim_fn=lambda _c: 1536)
    assert not check.ok
    assert "vector(1536)" in check.detail and "EMBED_DIM is 768" in check.detail
    assert "db/reset.sh" in check.detail


class FakeEmbedder:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def embed(self, texts, task_type):
        if self.error:
            raise self.error
        return self.result


def test_gemini_ok():
    check = check_gemini(SETTINGS, embedder_factory=lambda _s: FakeEmbedder(result=[[0.1] * 768]))
    assert check.ok
    assert check.detail == "gemini-embedding-001 returned 768 dims"


def test_gemini_failure_is_reported_not_raised():
    check = check_gemini(SETTINGS, embedder_factory=lambda _s: FakeEmbedder(error=RuntimeError("API key not valid")))
    assert not check.ok
    assert check.detail == "Embedding call failed: API key not valid"

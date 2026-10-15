import math
from types import SimpleNamespace

import httpx
import pytest

from gemini import BATCH_SIZE, Embedder, RateLimiter, is_retryable, l2_normalize


class FakeApiError(Exception):
    """Same shape as google.genai.errors.APIError: an int `code` attribute."""

    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class FakeModels:
    def __init__(self, dim, failures=(), wrong_dim=False):
        self.dim = dim
        self.failures = list(failures)
        self.wrong_dim = wrong_dim
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=list(contents), config=config))
        if self.failures:
            raise self.failures.pop(0)
        size = self.dim - 1 if self.wrong_dim else self.dim
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * (size - 2)) for _ in contents]
        )


def make_embedder(dim=4, **kwargs):
    models = FakeModels(dim, **kwargs)
    sleeps = []
    embedder = Embedder(SimpleNamespace(models=models), "gemini-embedding-001", dim, sleep=sleeps.append)
    return embedder, models, sleeps


def test_vectors_are_l2_normalised():
    embedder, _, _ = make_embedder()
    [vec] = embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")
    assert vec == pytest.approx([0.6, 0.8, 0.0, 0.0])
    assert math.isclose(sum(v * v for v in vec), 1.0)


def test_request_carries_model_task_type_and_dimension():
    embedder, models, _ = make_embedder()
    embedder.embed(["hello"], "RETRIEVAL_QUERY")
    call = models.calls[0]
    assert call.model == "gemini-embedding-001"
    assert call.config.task_type == "RETRIEVAL_QUERY"
    assert call.config.output_dimensionality == 4


def test_large_inputs_are_split_into_batches_of_100():
    embedder, models, _ = make_embedder()
    vectors = embedder.embed([f"text {i}" for i in range(250)], "RETRIEVAL_DOCUMENT")
    assert len(vectors) == 250
    assert [len(c.contents) for c in models.calls] == [BATCH_SIZE, BATCH_SIZE, 50]
    assert models.calls[2].contents[0] == "text 200"


def test_empty_list_makes_no_api_call():
    embedder, models, _ = make_embedder()
    assert embedder.embed([], "RETRIEVAL_DOCUMENT") == []
    assert models.calls == []


def test_blank_text_is_rejected_before_calling_the_api():
    embedder, models, _ = make_embedder()
    with pytest.raises(ValueError, match="empty text"):
        embedder.embed(["ok", "  "], "RETRIEVAL_DOCUMENT")
    assert models.calls == []


def test_rate_limit_is_retried_with_backoff():
    embedder, models, sleeps = make_embedder(failures=[FakeApiError(429)])
    assert len(embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")) == 1
    assert len(models.calls) == 2
    assert sleeps == [15.0]  # quota windows are per minute: never retry a 429 after only a second


def test_bad_request_fails_immediately():
    embedder, models, sleeps = make_embedder(failures=[FakeApiError(400)])
    with pytest.raises(FakeApiError):
        embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")
    assert len(models.calls) == 1
    assert sleeps == []


def test_gives_up_after_three_attempts():
    embedder, models, sleeps = make_embedder(failures=[FakeApiError(503)] * 3)
    with pytest.raises(FakeApiError):
        embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")
    assert len(models.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_wrong_dimension_from_api_is_an_error():
    embedder, _, _ = make_embedder(wrong_dim=True)
    with pytest.raises(RuntimeError, match="4 dims"):
        embedder.embed(["hello"], "RETRIEVAL_DOCUMENT")


def test_zero_vector_cannot_be_normalised():
    with pytest.raises(ValueError):
        l2_normalize([0.0, 0.0])


@pytest.mark.parametrize(
    "exc, expected",
    [
        (FakeApiError(429), True),
        (FakeApiError(503), True),
        (FakeApiError(400), False),
        (FakeApiError(403), False),
        (ConnectionError("reset"), True),
        (httpx.ConnectTimeout("timed out"), True),
        (ValueError("bad"), False),
    ],
)
def test_is_retryable(exc, expected):
    assert is_retryable(exc) is expected


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_waits_for_the_window_to_free_up():
    clock = FakeClock()
    limiter = RateLimiter(100, clock=lambda: clock.now, sleep=clock.sleep)
    limiter.acquire(100)
    clock.now = 10.0
    limiter.acquire(50)
    assert clock.sleeps == [50.0]  # the first 100 leave the 60s window at t=60
    with pytest.raises(ValueError):
        limiter.acquire(101)


def test_embedder_asks_the_limiter_before_each_batch():
    class RecordingLimiter:
        def __init__(self):
            self.asked = []

        def acquire(self, n):
            self.asked.append(n)

    limiter = RecordingLimiter()
    models = FakeModels(4)
    embedder = Embedder(SimpleNamespace(models=models), "gemini-embedding-001", 4, sleep=lambda s: None, limiter=limiter)
    embedder.embed([f"t{i}" for i in range(150)], "RETRIEVAL_DOCUMENT")
    assert limiter.asked == [100, 50]


class FakeGenModels:
    def __init__(self, texts=("A summary.",), failures=()):
        self.texts = list(texts)
        self.failures = list(failures)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(text=self.texts.pop(0))


def test_generator_returns_stripped_text_at_low_temperature():
    from gemini import Generator

    models = FakeGenModels(texts=["  Growth slows.  "])
    generator = Generator(SimpleNamespace(models=models), "gemini-3.8-flash", sleep=lambda s: None)
    assert generator.generate("Summarize") == "Growth slows."
    assert models.calls[0].model == "gemini-3.8-flash"
    assert models.calls[0].config.temperature == 0.2


def test_generator_rejects_empty_answers():
    from gemini import Generator

    generator = Generator(SimpleNamespace(models=FakeGenModels(texts=[None])), "m", sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="no text"):
        generator.generate("x")


def test_generator_waits_out_rate_limits_and_asks_the_limiter():
    from gemini import Generator

    sleeps, asked = [], []
    models = FakeGenModels(failures=[FakeApiError(429)])
    generator = Generator(SimpleNamespace(models=models), "m", sleep=sleeps.append, limiter=SimpleNamespace(acquire=asked.append))
    assert generator.generate("x") == "A summary."
    assert sleeps == [15.0]
    assert asked == [1]

"""Thin wrapper over google-genai: batched, L2-normalised embeddings with retry."""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable, Literal, Sequence, TypeVar

import httpx
from google import genai
from google.genai import types

from config import Settings

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
BATCH_SIZE = 100
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RATE_LIMIT_DELAY = 15.0  # quota windows are per minute; a 1s retry after a 429 just fails again
T = TypeVar("T")


def is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)  # google.genai.errors.APIError carries the HTTP status here
    if isinstance(code, int):
        return code in RETRYABLE_STATUS
    return isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError))


def with_retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = base_delay * 2 ** (attempt - 1)
            if getattr(exc, "code", None) == 429:
                delay = max(delay, RATE_LIMIT_DELAY * attempt)
            sleep(delay)
    raise AssertionError("unreachable")


def l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        raise ValueError("cannot normalise a zero vector")
    return [v / norm for v in vec]


class RateLimiter:
    """At most `per_minute` texts in any rolling 60-second window."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[tuple[float, int]] = deque()

    def acquire(self, n: int) -> None:
        if n > self.per_minute:
            raise ValueError(f"batch of {n} exceeds the {self.per_minute}/minute limit")
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0][0] >= 60:
                self._sent.popleft()
            if sum(count for _, count in self._sent) + n <= self.per_minute:
                self._sent.append((now, n))
                return
            self._sleep(60 - (now - self._sent[0][0]))


class Embedder:
    def __init__(self, client, model: str, dim: int, sleep: Callable[[float], None] = time.sleep, limiter: RateLimiter | None = None):
        self._client = client
        self._model = model
        self._dim = dim
        self._sleep = sleep
        self._limiter = limiter

    def embed(self, texts: Sequence[str], task_type: TaskType) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise ValueError("cannot embed empty text")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(self._embed_batch(list(texts[start : start + BATCH_SIZE]), task_type))
        return vectors

    def _embed_batch(self, batch: list[str], task_type: TaskType) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._dim)
        if self._limiter is not None:
            self._limiter.acquire(len(batch))
        response = with_retry(
            lambda: self._client.models.embed_content(model=self._model, contents=batch, config=config),
            sleep=self._sleep,
        )
        values = [embedding.values for embedding in response.embeddings]
        if len(values) != len(batch) or any(len(v) != self._dim for v in values):
            raise RuntimeError(f"expected {len(batch)} embeddings of {self._dim} dims from {self._model}")
        return [l2_normalize(v) for v in values]


def make_client(settings: Settings) -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def make_embedder(settings: Settings) -> Embedder:
    return Embedder(make_client(settings), settings.embed_model, settings.embed_dim, limiter=RateLimiter(settings.embed_per_minute))

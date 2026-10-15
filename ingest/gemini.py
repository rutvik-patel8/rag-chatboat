"""Thin wrapper over google-genai: batched, L2-normalised embeddings with retry."""
from __future__ import annotations

import json
import math
import time
from collections import deque
from typing import Callable, Literal, Sequence, TypeVar

import httpx
from google import genai
from google.genai import types

from chunk.common import count_tokens
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
    def __init__(
        self,
        client,
        model: str,
        dim: int,
        sleep: Callable[[float], None] = time.sleep,
        limiter: RateLimiter | None = None,
        token_limiter: RateLimiter | None = None,
        count: Callable[[str], int] = count_tokens,
    ):
        self._client = client
        self._model = model
        self._dim = dim
        self._sleep = sleep
        self._limiter = limiter  # texts per minute
        self._token_limiter = token_limiter  # tokens per minute; also caps one batch's size
        self._count = count

    def embed(self, texts: Sequence[str], task_type: TaskType) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise ValueError("cannot embed empty text")
        vectors: list[list[float]] = []
        for batch, tokens in self._batches(texts):
            vectors.extend(self._embed_batch(batch, tokens, task_type))
        return vectors

    def _batches(self, texts: Sequence[str]) -> list[tuple[list[str], int]]:
        budget = self._token_limiter.per_minute if self._token_limiter is not None else math.inf
        batches: list[tuple[list[str], int]] = []
        current: list[str] = []
        tokens = 0
        for text in texts:
            n = self._count(text)
            if current and (len(current) == BATCH_SIZE or tokens + n > budget):
                batches.append((current, tokens))
                current, tokens = [], 0
            current.append(text)
            tokens += n
        if current:
            batches.append((current, tokens))
        return batches

    def _embed_batch(self, batch: list[str], tokens: int, task_type: TaskType) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._dim)
        if self._limiter is not None:
            self._limiter.acquire(len(batch))
        if self._token_limiter is not None:
            self._token_limiter.acquire(min(tokens, self._token_limiter.per_minute))
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
    return Embedder(
        make_client(settings),
        settings.embed_model,
        settings.embed_dim,
        limiter=RateLimiter(settings.embed_per_minute),
        token_limiter=RateLimiter(settings.embed_tokens_per_minute),
    )


class Generator:
    """Text generation for ingest-time enrichment (section summaries)."""

    def __init__(self, client, model: str, sleep: Callable[[float], None] = time.sleep, limiter: RateLimiter | None = None):
        self._client = client
        self._model = model
        self._sleep = sleep
        self._limiter = limiter

    def generate(self, prompt: str) -> str:
        if self._limiter is not None:
            self._limiter.acquire(1)
        config = types.GenerateContentConfig(temperature=0.2)
        response = with_retry(
            lambda: self._client.models.generate_content(model=self._model, contents=prompt, config=config),
            sleep=self._sleep,
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError(f"{self._model} returned no text")
        return text


    def generate_json(self, prompt: str, schema: dict, image_png: bytes | None = None) -> dict:
        """Structured output: the answer must parse as JSON matching `schema` (optionally about one image)."""
        if self._limiter is not None:
            self._limiter.acquire(1)
        contents = [types.Part.from_bytes(data=image_png, mime_type="image/png"), prompt] if image_png else prompt
        config = types.GenerateContentConfig(temperature=0, response_mime_type="application/json", response_schema=schema)
        response = with_retry(
            lambda: self._client.models.generate_content(model=self._model, contents=contents, config=config),
            sleep=self._sleep,
        )
        if not (response.text or "").strip():
            raise RuntimeError(f"{self._model} returned no text")
        return json.loads(response.text)


def make_generator(settings: Settings, model: str | None = None) -> Generator:
    return Generator(make_client(settings), model or settings.chat_model, limiter=RateLimiter(settings.chat_per_minute))

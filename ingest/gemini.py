"""Thin wrapper over google-genai: batched, L2-normalised embeddings with retry."""
from __future__ import annotations

import math
import time
from typing import Callable, Literal, Sequence, TypeVar

import httpx
from google import genai
from google.genai import types

from config import Settings

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
BATCH_SIZE = 100
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
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
            sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        raise ValueError("cannot normalise a zero vector")
    return [v / norm for v in vec]


class Embedder:
    def __init__(self, client, model: str, dim: int, sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model = model
        self._dim = dim
        self._sleep = sleep

    def embed(self, texts: Sequence[str], task_type: TaskType) -> list[list[float]]:
        if any(not text.strip() for text in texts):
            raise ValueError("cannot embed empty text")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(self._embed_batch(list(texts[start : start + BATCH_SIZE]), task_type))
        return vectors

    def _embed_batch(self, batch: list[str], task_type: TaskType) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._dim)
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
    return Embedder(make_client(settings), settings.embed_model, settings.embed_dim)

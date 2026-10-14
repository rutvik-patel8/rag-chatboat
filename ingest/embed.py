"""Embed chunk texts through the disk cache so re-runs cost nothing."""
from __future__ import annotations

from cache import JsonCache
from gemini import BATCH_SIZE


def embed_texts(texts: list[str], embedder, cache: JsonCache, model: str, dim: int) -> list[list[float]]:
    keys = [JsonCache.key(model, str(dim), text) for text in texts]
    missing = [i for i, key in enumerate(keys) if cache.get(key) is None]
    for start in range(0, len(missing), BATCH_SIZE):  # cache each batch so a later failure loses nothing paid for
        batch = missing[start : start + BATCH_SIZE]
        vectors = embedder.embed([texts[i] for i in batch], "RETRIEVAL_DOCUMENT")
        for i, vector in zip(batch, vectors):
            cache.put(keys[i], vector)
    return [cache.get(key) for key in keys]

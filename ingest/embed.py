"""Embed chunk texts through the disk cache so re-runs cost nothing."""
from __future__ import annotations

from cache import JsonCache


def embed_texts(texts: list[str], embedder, cache: JsonCache, model: str, dim: int) -> list[list[float]]:
    keys = [JsonCache.key(model, str(dim), text) for text in texts]
    missing = [i for i, key in enumerate(keys) if cache.get(key) is None]
    if missing:
        vectors = embedder.embed([texts[i] for i in missing], "RETRIEVAL_DOCUMENT")
        for i, vector in zip(missing, vectors):
            cache.put(keys[i], vector)
    return [cache.get(key) for key in keys]

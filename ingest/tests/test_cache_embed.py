from cache import JsonCache
from embed import embed_texts


def test_put_get_roundtrip_and_missing_key(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    assert cache.get("nope") is None
    cache.put("k1", [0.6, 0.8])
    assert JsonCache(tmp_path, "imac/embeddings").get("k1") == [0.6, 0.8]


def test_clear_removes_only_its_namespace(tmp_path):
    imac, gep = JsonCache(tmp_path, "imac"), JsonCache(tmp_path, "gep")
    JsonCache(tmp_path, "imac/embeddings").put("a", 1)
    JsonCache(tmp_path, "imac/vision").put("b", 2)
    gep.put("c", 3)
    assert imac.clear() == 2
    assert JsonCache(tmp_path, "imac/embeddings").get("a") is None
    assert gep.get("c") == 3


def test_key_is_stable_and_order_sensitive():
    assert JsonCache.key("m", "768", "text") == JsonCache.key("m", "768", "text")
    assert JsonCache.key("m", "768", "text") != JsonCache.key("768", "m", "text")


class CountingEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts, task_type):
        self.calls.append((list(texts), task_type))
        return [[float(len(t)), 1.0] for t in texts]


def test_only_cache_misses_are_embedded_and_order_is_kept(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    embedder = CountingEmbedder()
    embed_texts(["bb"], embedder, cache, "m", 2)
    vectors = embed_texts(["a", "bb", "ccc"], embedder, cache, "m", 2)
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    assert embedder.calls == [(["bb"], "RETRIEVAL_DOCUMENT"), (["a", "ccc"], "RETRIEVAL_DOCUMENT")]


def test_second_run_makes_no_api_calls(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    embedder = CountingEmbedder()
    embed_texts(["a", "b"], embedder, cache, "m", 2)
    embed_texts(["a", "b"], embedder, cache, "m", 2)
    assert len(embedder.calls) == 1


def test_changing_model_or_dim_re_embeds(tmp_path):
    cache = JsonCache(tmp_path, "imac/embeddings")
    embedder = CountingEmbedder()
    embed_texts(["a"], embedder, cache, "m", 2)
    embed_texts(["a"], embedder, cache, "other-model", 2)
    embed_texts(["a"], embedder, cache, "m", 3)
    assert len(embedder.calls) == 3


class FailingSecondBatch(CountingEmbedder):
    def embed(self, texts, task_type):
        if self.calls:
            raise RuntimeError("429 quota")
        return super().embed(texts, task_type)


def test_finished_batches_stay_cached_when_a_later_batch_fails(tmp_path):
    cache = JsonCache(tmp_path, "gpo/embeddings")
    texts = [f"text {i}" for i in range(150)]
    try:
        embed_texts(texts, FailingSecondBatch(), cache, "m", 2)
    except RuntimeError:
        pass
    retry = CountingEmbedder()
    embed_texts(texts, retry, cache, "m", 2)
    assert [len(batch) for batch, _ in retry.calls] == [50]

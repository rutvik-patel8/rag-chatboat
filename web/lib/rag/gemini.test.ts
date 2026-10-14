import { describe, expect, it } from "vitest";
import { createEmbedder, isRetryable, l2Normalize, MAX_BATCH, type EmbedClient } from "@/lib/rag/gemini";

class FakeApiError extends Error {
  // Same shape as @google/genai's ApiError: a numeric `status`.
  constructor(public status: number) {
    super(`HTTP ${status}`);
  }
}

function setup({ dim = 4, failures = [] as Error[], wrongDim = false } = {}) {
  const calls: Parameters<EmbedClient["models"]["embedContent"]>[0][] = [];
  const sleeps: number[] = [];
  const client: EmbedClient = {
    models: {
      async embedContent(req) {
        calls.push(req);
        const failure = failures.shift();
        if (failure) throw failure;
        const size = wrongDim ? dim - 1 : dim;
        return { embeddings: req.contents.map(() => ({ values: [3, 4, ...Array(size - 2).fill(0)] })) };
      },
    },
  };
  const embedder = createEmbedder({
    client,
    model: "gemini-embedding-001",
    dim,
    sleep: async (ms) => {
      sleeps.push(ms);
    },
  });
  return { embedder, calls, sleeps };
}

describe("createEmbedder", () => {
  it("returns L2-normalised vectors", async () => {
    const { embedder } = setup();
    const [vec] = await embedder.embed(["hello"], "RETRIEVAL_QUERY");
    expect(vec[0]).toBeCloseTo(0.6);
    expect(vec[1]).toBeCloseTo(0.8);
  });

  it("sends model, task type and output dimensionality", async () => {
    const { embedder, calls } = setup();
    await embedder.embed(["hello"], "RETRIEVAL_QUERY");
    expect(calls[0]).toEqual({
      model: "gemini-embedding-001",
      contents: ["hello"],
      config: { taskType: "RETRIEVAL_QUERY", outputDimensionality: 4 },
    });
  });

  it("returns [] for no texts without calling the API", async () => {
    const { embedder, calls } = setup();
    expect(await embedder.embed([], "RETRIEVAL_QUERY")).toEqual([]);
    expect(calls).toHaveLength(0);
  });

  it("rejects blank text and oversized batches before calling the API", async () => {
    const { embedder, calls } = setup();
    await expect(embedder.embed(["  "], "RETRIEVAL_QUERY")).rejects.toThrow("empty text");
    await expect(embedder.embed(Array(MAX_BATCH + 1).fill("x"), "RETRIEVAL_QUERY")).rejects.toThrow("at most 100");
    expect(calls).toHaveLength(0);
  });

  it("retries a 429 with backoff", async () => {
    const { embedder, calls, sleeps } = setup({ failures: [new FakeApiError(429)] });
    await embedder.embed(["hello"], "RETRIEVAL_QUERY");
    expect(calls).toHaveLength(2);
    expect(sleeps).toEqual([1000]);
  });

  it("fails immediately on a 400", async () => {
    const { embedder, calls, sleeps } = setup({ failures: [new FakeApiError(400)] });
    await expect(embedder.embed(["hello"], "RETRIEVAL_QUERY")).rejects.toThrow("HTTP 400");
    expect(calls).toHaveLength(1);
    expect(sleeps).toEqual([]);
  });

  it("gives up after three attempts", async () => {
    const failures = [new FakeApiError(503), new FakeApiError(503), new FakeApiError(503)];
    const { embedder, calls, sleeps } = setup({ failures });
    await expect(embedder.embed(["hello"], "RETRIEVAL_QUERY")).rejects.toThrow("HTTP 503");
    expect(calls).toHaveLength(3);
    expect(sleeps).toEqual([1000, 2000]);
  });

  it("errors when the API returns the wrong dimension", async () => {
    const { embedder } = setup({ wrongDim: true });
    await expect(embedder.embed(["hello"], "RETRIEVAL_QUERY")).rejects.toThrow("4 dims");
  });
});

describe("isRetryable", () => {
  it.each([
    [new FakeApiError(429), true],
    [new FakeApiError(500), true],
    [new FakeApiError(400), false],
    [new FakeApiError(403), false],
    [new TypeError("fetch failed"), true],
    [new Error("other"), false],
  ])("%s -> %s", (err, expected) => {
    expect(isRetryable(err)).toBe(expected);
  });
});

describe("l2Normalize", () => {
  it("rejects a zero vector", () => {
    expect(() => l2Normalize([0, 0])).toThrow();
  });
});

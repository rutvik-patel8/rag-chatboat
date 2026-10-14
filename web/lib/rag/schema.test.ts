import { describe, expect, it } from "vitest";
import { evaluateSchema } from "@/lib/rag/schema";

const expected = { embedModel: "gemini-embedding-001", embedDim: 768 };

describe("evaluateSchema", () => {
  it("accepts a matching column and matching documents", () => {
    const facts = {
      vectorType: "vector(768)",
      documents: [{ slug: "gpo", embed_model: "gemini-embedding-001", embed_dim: 768 }],
    };
    expect(evaluateSchema(facts, expected)).toEqual([]);
  });

  it("reports a missing chunks table", () => {
    const [problem] = evaluateSchema({ vectorType: null, documents: [] }, expected);
    expect(problem).toContain("db/reset.sh");
  });

  it("reports a column dimension that differs from EMBED_DIM", () => {
    const [problem] = evaluateSchema({ vectorType: "vector(1536)", documents: [] }, expected);
    expect(problem).toBe("chunks.embedding is vector(1536) but EMBED_DIM is 768; run db/reset.sh");
  });

  it("reports documents ingested with a different embedding model or dimension", () => {
    const facts = {
      vectorType: "vector(768)",
      documents: [{ slug: "gep", embed_model: "text-embedding-004", embed_dim: 768 }],
    };
    const [problem] = evaluateSchema(facts, expected);
    expect(problem).toContain('"gep"');
    expect(problem).toContain("re-run ingestion");
  });
});

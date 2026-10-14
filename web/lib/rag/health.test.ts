import { describe, expect, it } from "vitest";
import { errorMessage, runHealthCheck } from "@/lib/rag/health";

const expected = { embedModel: "gemini-embedding-001", embedDim: 768 };

describe("runHealthCheck", () => {
  it("returns 200 with document slugs when everything matches", async () => {
    const result = await runHealthCheck(
      async () => ({
        vectorType: "vector(768)",
        documents: [{ slug: "gpo", embed_model: "gemini-embedding-001", embed_dim: 768 }],
      }),
      expected,
    );
    expect(result).toEqual({ status: 200, body: { ok: true, problems: [], documents: ["gpo"] } });
  });

  it("returns 500 with problems on a schema mismatch", async () => {
    const result = await runHealthCheck(async () => ({ vectorType: "vector(1536)", documents: [] }), expected);
    expect(result.status).toBe(500);
    expect(result.body.ok).toBe(false);
    expect(result.body.problems[0]).toContain("vector(1536)");
  });

  it("returns 503 when the database is unreachable", async () => {
    const result = await runHealthCheck(async () => {
      throw new Error("connect ECONNREFUSED 127.0.0.1:5433");
    }, expected);
    expect(result.status).toBe(503);
    expect(result.body.problems).toEqual(["Database unavailable: connect ECONNREFUSED 127.0.0.1:5433"]);
  });
});

describe("errorMessage", () => {
  it("falls back to the error code when the message is empty (Node AggregateError)", () => {
    const err = Object.assign(new AggregateError([], ""), { code: "ECONNREFUSED" });
    expect(errorMessage(err)).toBe("ECONNREFUSED");
  });

  it("stringifies non-Error values", () => {
    expect(errorMessage("boom")).toBe("boom");
  });
});

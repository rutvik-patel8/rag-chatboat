import { describe, expect, it } from "vitest";
import { getEnv } from "@/lib/env";
import { embedQuery } from "@/lib/rag/gemini";

describe.skipIf(!process.env.RUN_LIVE)("embedQuery (real Gemini)", () => {
  it("returns a unit-length vector of EMBED_DIM", async () => {
    const vec = await embedQuery("What is the growth outlook for South Asia?");
    expect(vec).toHaveLength(getEnv().EMBED_DIM);
    expect(Math.sqrt(vec.reduce((s, x) => s + x * x, 0))).toBeCloseTo(1, 5);
  });
});

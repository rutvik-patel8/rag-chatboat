import { afterAll, describe, expect, it } from "vitest";
import { getEnv } from "@/lib/env";
import { getPool } from "@/lib/rag/db";
import { readSchemaFacts } from "@/lib/rag/schema";

describe.skipIf(!process.env.RUN_INTEGRATION)("readSchemaFacts (live DB)", () => {
  afterAll(async () => {
    await getPool().end();
  });

  it("reads the vector column type from the real schema", async () => {
    const facts = await readSchemaFacts(getPool());
    expect(facts.vectorType).toBe(`vector(${getEnv().EMBED_DIM})`);
    expect(Array.isArray(facts.documents)).toBe(true);
  });
});

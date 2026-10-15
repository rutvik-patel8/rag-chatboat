import { describe, expect, it } from "vitest";
import { memoCatalog } from "@/lib/rag/deps";
import type { Catalog } from "@/lib/rag/router";

describe("memoCatalog", () => {
  it("caches a loaded catalog but retries after a failed or empty load", async () => {
    const results: (Catalog | Error)[] = [new Error("db down"), {}, { gep: { title: "GEP", sections: [] } }];
    let calls = 0;
    const catalog = memoCatalog(async () => {
      const next = results[calls++];
      if (next instanceof Error) throw next;
      return next;
    });
    await expect(catalog()).rejects.toThrow("db down");
    expect(await catalog()).toEqual({});
    expect(Object.keys(await catalog())).toEqual(["gep"]);
    expect(Object.keys(await catalog())).toEqual(["gep"]);
    expect(calls).toBe(3);
  });
});

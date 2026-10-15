import { afterAll, describe, expect, it } from "vitest";
import { buildBlocks, loadNeighbours } from "@/lib/rag/context";
import { getPool } from "@/lib/rag/db";
import { hybridSearch, loadCatalog } from "@/lib/rag/retrieve";

const SAR = "Ch. 2 Regional Outlooks › South Asia";

// Uses a stored chunk's embedding as the query vector, so no embedding API call is needed.
async function vectorOf(like: string): Promise<number[]> {
  const { rows } = await getPool().query<{ v: string }>(
    "SELECT c.embedding::text AS v FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.slug = 'gep' AND c.content LIKE $1 LIMIT 1",
    [like],
  );
  return JSON.parse(rows[0].v);
}

describe.skipIf(!process.env.RUN_INTEGRATION)("hybrid search (live DB)", () => {
  afterAll(async () => {
    await getPool().end();
  });

  it("finds the South Asia outlook and stays inside the routed region", async () => {
    const vector = await vectorOf("Growth in SAR is expected%");
    const found = await hybridSearch(getPool(), { vector, question: "growth outlook South Asia", docs: ["gep"], paths: [SAR] });
    expect(found.slice(0, 3).some((c) => c.content.includes("Growth in SAR is expected to soften"))).toBe(true);
    expect(found.every((c) => c.sectionPath === SAR || c.sectionPath.startsWith(`${SAR} › `))).toBe(true);
    expect(found.some((c) => c.vecRank !== null && c.kwRank !== null)).toBe(true);
  });

  it("searches by vector alone when keyword search is off (the vectorOnly ablation)", async () => {
    const vector = await vectorOf("Growth in SAR is expected%");
    const found = await hybridSearch(getPool(), { vector, question: "growth outlook South Asia", docs: ["gep"], paths: [SAR], keyword: false });
    expect(found.length).toBeGreaterThan(0);
    expect(found.every((c) => c.kwRank === null && c.vecRank !== null)).toBe(true);
  });

  it("restricts to the requested documents and loads text neighbours", async () => {
    const vector = await vectorOf("Growth in SAR is expected%");
    const found = await hybridSearch(getPool(), { vector, question: "growth", docs: ["gpo"], paths: [] });
    expect(found.length).toBeGreaterThan(0);
    expect(found.every((c) => c.docSlug === "gpo")).toBe(true);
    const neighbours = await loadNeighbours(getPool(), found.slice(0, 3));
    const blocks = buildBlocks(found.slice(0, 3), neighbours);
    expect(blocks[0].chunkIds.length).toBeGreaterThanOrEqual(1);
  });

  it("loads the section catalog for routing", async () => {
    const catalog = await loadCatalog(getPool());
    expect(catalog.gep.sections.some((s) => s.path === SAR && s.kind === "region")).toBe(true);
    expect(Object.keys(catalog).sort()).toEqual(["gep", "gpo", "imac"]);
  });
});

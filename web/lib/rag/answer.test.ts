import { describe, expect, it } from "vitest";
import { answer, refusal, type ChatEvent, type Deps } from "@/lib/rag/answer";
import type { Catalog } from "@/lib/rag/router";
import { candidate } from "@/lib/rag/testing";

const catalog: Catalog = {
  gep: { title: "Global Economic Prospects", sections: [{ path: "Ch. 2 Regional Outlooks › South Asia", level: 2, kind: "region" }] },
};

function deps(overrides: Partial<Deps> = {}): Deps & { searches: unknown[] } {
  const searches: unknown[] = [];
  return {
    searches,
    catalog: async () => catalog,
    condense: async (_history, question) => question,
    embed: async () => [0.1, 0.2],
    search: async (args) => {
      searches.push(args);
      return [candidate(1, { content: "Growth in SAR is expected to soften to 6.3 percent in 2026." }), candidate(2)];
    },
    rerank: async () => new Map([[1, 3], [2, 0]]),
    neighbours: async () => new Map(),
    async *generate() {
      yield "Growth will soften to 6.3 percent ";
      yield "in 2026 [S1].";
    },
    ...overrides,
  };
}

async function run(d: Deps, question = "What is the growth outlook for South Asia?") {
  const events: ChatEvent[] = [];
  await answer({ question, scope: "gep", history: [] }, d, (e) => events.push(e));
  return events;
}

describe("answer", () => {
  it("routes, retrieves, streams sources then text, and verifies the answer", async () => {
    const d = deps();
    const events = await run(d);
    expect(events.map((e) => e.type)).toEqual(["sources", "text", "text", "done"]);
    expect(d.searches[0]).toMatchObject({ docs: ["gep"], paths: ["Ch. 2 Regional Outlooks › South Asia"], vector: [0.1, 0.2] });
    const sources = events[0] as Extract<ChatEvent, { type: "sources" }>;
    expect(sources.sources.map((s) => s.id)).toEqual([1]);
    const done = events.at(-1) as Extract<ChatEvent, { type: "done" }>;
    expect(done.warnings).toEqual([]);
    expect(done.debug.candidates.map((c) => c.rerank)).toEqual([3, 0]);
  });

  it("refuses without calling the answer model when nothing is relevant", async () => {
    let generated = false;
    const events = await run(deps({
      rerank: async () => new Map([[1, 1], [2, 0]]),
      async *generate() {
        generated = true;
        yield "";
      },
    }));
    expect(generated).toBe(false);
    expect(events.map((e) => e.type)).toEqual(["sources", "text", "done"]);
    expect((events[1] as { delta: string }).delta).toBe(
      "I couldn't find this in Global Economic Prospects. Closest sections: Global Economic Prospects › Ch. 2 Regional Outlooks › South Asia.",
    );
  });

  it("falls back to search order when reranking fails, and reports unverified figures", async () => {
    const events = await run(deps({
      rerank: async () => {
        throw new Error("429");
      },
      async *generate() {
        yield "Growth will be 9.9 percent [S1].";
      },
    }));
    const done = events.at(-1) as Extract<ChatEvent, { type: "done" }>;
    expect(done.warnings).toEqual(["reranking failed; using search order", "unverified figures: 9.9"]);
  });

  it("reports a generation failure as an error event", async () => {
    const events = await run(deps({
      async *generate() {
        throw new Error("quota exceeded");
      },
    }));
    expect(events.at(-1)).toEqual({ type: "error", message: "answer generation failed: quota exceeded" });
  });

  it("rewrites follow-up questions before routing", async () => {
    const d = deps({ condense: async () => "What is the growth outlook for South Asia?" });
    const events: ChatEvent[] = [];
    await answer({ question: "and for that region?", scope: "gep", history: [{ role: "user", content: "hi" }] }, d, (e) => events.push(e));
    expect(d.searches[0]).toMatchObject({ paths: ["Ch. 2 Regional Outlooks › South Asia"] });
  });
});

describe("refusal", () => {
  it("names the document alone when the closest text sits outside any section", () => {
    const closest = [candidate(1, { docTitle: "iMac Report", sectionPath: "" }), candidate(2, { docTitle: "iMac Report" })];
    expect(refusal("imac", { imac: { title: "iMac Report", sections: [] } }, closest)).toBe(
      "I couldn't find this in iMac Report. Closest sections: iMac Report; iMac Report › Ch. 2 Regional Outlooks › South Asia.",
    );
  });
});

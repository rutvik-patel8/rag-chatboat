import { describe, expect, it } from "vitest";
import { answer, refusal, type ChatEvent, type Deps } from "@/lib/rag/answer";
import { SYSTEM_PROMPT } from "@/lib/rag/prompt";
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
    expect(sources.sources[0]).toMatchObject({ slug: "gep", doc: "Global Economic Prospects", pageStart: 96, text: expect.stringContaining("6.3 percent") });
    const done = events.at(-1) as Extract<ChatEvent, { type: "done" }>;
    expect(done.warnings).toEqual([]);
    expect(done.debug.candidates.map((c) => c.rerank)).toEqual([3, 0]);
  });

  it("replies to a greeting without condensing, searching or generating", async () => {
    const called: string[] = [];
    const events = await run(deps({
      condense: async () => (called.push("condense"), "hello"),
      embed: async () => (called.push("embed"), []),
      search: async () => (called.push("search"), []),
      async *generate() {
        called.push("generate");
        yield "";
      },
    }), "Hello!");
    expect(called).toEqual([]);
    expect(events.map((e) => e.type)).toEqual(["sources", "text", "done"]);
    expect((events[1] as Extract<ChatEvent, { type: "text" }>).delta).toContain("Global Economic Prospects");
    expect(events[2]).toMatchObject({ warnings: [], debug: { question: "Hello!", route: { docs: [], paths: [] }, candidates: [] } });
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
        throw new Error('{"error":{"code":429,"message":"Quota exceeded.","status":"RESOURCE_EXHAUSTED"}}');
      },
    }));
    expect(events.at(-1)).toEqual({ type: "error", message: "answer generation failed: Quota exceeded. (429 RESOURCE_EXHAUSTED)" });
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

describe("SYSTEM_PROMPT", () => {
  it("tells the model to refuse when no source answers, e.g. when reranking failed", () => {
    expect(SYSTEM_PROMPT).toContain("If none of the sources answer the question, reply only: I couldn't find this in the documents.");
  });
});

describe("ablation options", () => {
  it("noRouter drops section filters, vectorOnly drops keyword search, noRerank keeps search order", async () => {
    let reranked = false;
    const d = deps({
      rerank: async () => {
        reranked = true;
        return new Map();
      },
    });
    const events: ChatEvent[] = [];
    const options = { noRouter: true, vectorOnly: true, noRerank: true };
    await answer({ question: "What is the growth outlook for South Asia?", scope: "gep", history: [], options }, d, (e) => events.push(e));
    expect(d.searches[0]).toMatchObject({ docs: ["gep"], paths: [], keyword: false });
    expect(reranked).toBe(false);
    const sources = events[0] as Extract<ChatEvent, { type: "sources" }>;
    expect(sources.sources.map((s) => s.id)).toEqual([1, 2]); // both candidates, no relevance gate
    expect((events.at(-1) as Extract<ChatEvent, { type: "done" }>).warnings).toEqual([]);
  });

  it("noFootnotes leaves footnotes out of the sources the model sees", async () => {
    const prompts: string[] = [];
    const d = deps({
      search: async () => [candidate(1, { content: "Uses 58 percent less energy.18", footnotes: [{ n: 18, text: "ENERGY STAR test." }] })],
      rerank: async () => new Map([[1, 3]]),
      async *generate(_system, prompt) {
        prompts.push(prompt);
        yield "58 percent less [S1].";
      },
    });
    await answer({ question: "q", scope: "imac", history: [], options: { noFootnotes: true } }, d, () => {});
    await answer({ question: "q", scope: "imac", history: [] }, d, () => {});
    expect(prompts[0]).not.toContain("ENERGY STAR test.");
    expect(prompts[1]).toContain("Footnotes:\n18. ENERGY STAR test.");
  });
});

# Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `npm run eval` scores the live pipeline on 18 questions sourced from the PDFs and checks the spec's pass bar. `npm run eval -- --ablate` shows what each technique (keyword search, reranking, routing, footnotes) contributes per document shape.

**Architecture:**
- **Pipeline switches:** the pipeline gains four ablation switches, honoured only outside production, and a chat-model override.
- **`web/lib/eval/`** holds pure, unit-tested pieces:
  - question loading (`eval/questions.yaml`, zod-validated);
  - scoring;
  - a Gemini rubric judge;
  - a runner that drives the real `answer()` orchestrator;
  - text reports.
- **The CLI** is a thin entry point, `web/scripts/eval.ts`, run with `tsx`. It wires production dependencies and writes `eval/results/<timestamp>.json`.

**Tech Stack:** TypeScript, vitest, zod, `yaml` (new dev dependency), `tsx` (new dev dependency), the sub-project 6 pipeline, Gemini (Flash for the main run, Flash-Lite for ablations and the judge).

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`. This plan implements §8 (updated in commit `ebcc928`: metric definitions, Lite ablations, layout) and §10 row 8. The pass bar is §1.

## Global Constraints

- Run every npm command from `web/`. The Docker DB must be up for `npm run test:int` and for live runs.
- **Questions:** 18 = 9 brief + 4 coverage + 4 trap + 1 isolation. `must_contain`, `must_not_contain` and `rubric` values come from the PDF pages named in `expected_pages`, never from memory.
- **Models:** the main run answers with `GEMINI_CHAT_MODEL`. Ablations and the judge use `utilityModel(env)`.
- **Results:** `eval/results/` is git-ignored. The command exits 1 when the pass bar is not met, and 2 on bad arguments.
- **Ablation switches:** `options: {vectorOnly?, noRerank?, noRouter?, noFootnotes?}` is dropped when `NODE_ENV === "production"`. Unknown keys get HTTP 400.
- **Free tier:** a main run costs about 14 Flash answers (traps are refused before generation) of roughly 20 a day, plus 2 Lite judge calls. A full ablation run costs about 70 Lite answers and 72 Lite reranks; 18 embeddings are shared across configs.
- **Commits:** one per task, short imperative subject only (≤ 50 chars). **No `Co-Authored-By` trailer, no body.**

## Review Focus

1. **Figures written differently** ("6.3 percent" vs "6.3%", "802,834", "$375.7 million" for `375`) must still match, and a missing fact must still fail. Pinned in Task 3.
2. **A Gemini 503 or 429 mid-run** (common on the free tier) marks that question as an error, and the run continues and still writes its results file. A failed judge call doesn't sink the run. Pinned in Tasks 4 and 5.
3. **Ablation switches in production** must never take effect. Only the four known switches are accepted. Pinned in Task 1.
4. **A broken questions file** (missing facts, unknown fields, duplicate ids) or an unknown `--only` id or `--config` fails loudly, before any Gemini call. Pinned in Tasks 2 and 5.
5. **The isolation check** catches a South Asia source or a South Asia-only figure in an East Asia answer. Pinned in Task 3.

---

### Task 1: Ablation switches in the pipeline

**Files:**
- Modify (whole files given): `web/lib/rag/answer.ts`, `web/lib/rag/router.ts`, `web/lib/rag/retrieve.ts`, `web/lib/rag/context.ts`, `web/lib/rag/request.ts`, `web/lib/rag/deps.ts`
- Test (whole files given): `web/lib/rag/answer.test.ts`, `web/lib/rag/request.test.ts`, `web/lib/rag/router.test.ts`, `web/lib/rag/context.test.ts`, `web/lib/rag/retrieve.int.test.ts`

**Interfaces:**
- Produces:
  - `Options = { vectorOnly?; noRerank?; noRouter?; noFootnotes? }`, `ChatRequest.options?: Options`
  - `scopeRoute(scope, catalog): Route`
  - `SearchArgs.keyword?: boolean` (`false` means vector search only)
  - `buildBlocks(picked, neighbours, budget?, { footnotes? })`
  - `productionDeps(signal?, { chatModel? })`
  - `parseChatRequest` drops `options` in production

- [ ] **Step 1: Write the failing tests.** Replace the whole of `web/lib/rag/answer.test.ts` with:

```ts
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
```

Replace the whole of `web/lib/rag/request.test.ts` with:

```ts
import { describe, expect, it, vi } from "vitest";
import { parseChatRequest } from "@/lib/rag/request";

describe("parseChatRequest", () => {
  it("fills defaults and trims the question", () => {
    expect(parseChatRequest({ question: "  Growth in SAR?  " })).toEqual({ ok: true, request: { question: "Growth in SAR?", scope: "all", history: [] } });
  });

  it("keeps the last 6 turns of a long conversation, including long answers", () => {
    const history = Array.from({ length: 8 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: `turn ${i} ${"x".repeat(i === 7 ? 5000 : 0)}` }));
    const parsed = parseChatRequest({ question: "and then?", history });
    expect(parsed.ok && parsed.request.history.map((t) => t.content.slice(0, 6))).toEqual(["turn 2", "turn 3", "turn 4", "turn 5", "turn 6", "turn 7"]);
  });

  it("accepts ablation options outside production only", () => {
    const body = { question: "q", options: { vectorOnly: true, noRerank: true } };
    expect(parseChatRequest(body)).toMatchObject({ ok: true, request: { options: { vectorOnly: true, noRerank: true } } });
    expect(parseChatRequest({ question: "q", options: { noCache: true } })).toMatchObject({ ok: false, error: expect.stringContaining("options") });
    vi.stubEnv("NODE_ENV", "production");
    try {
      const parsed = parseChatRequest(body);
      expect(parsed.ok && parsed.request.options).toBeUndefined();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("rejects empty, oversized or malformed requests with a readable reason", () => {
    expect(parseChatRequest({ question: " " })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x".repeat(1001) })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x", scope: "everything" })).toMatchObject({ ok: false, error: expect.stringContaining("scope") });
    expect(parseChatRequest(null)).toMatchObject({ ok: false });
  });
});
```

Replace the whole of `web/lib/rag/router.test.ts` with:

```ts
import { describe, expect, it } from "vitest";
import { routeQuestion, scopeRoute, type Catalog } from "@/lib/rag/router";

const CH2 = "Ch. 2 Regional Outlooks";
const catalog: Catalog = {
  gpo: { title: "GPO 2018 Annual Report", sections: [{ path: "", level: 0, kind: "section" }] },
  imac: { title: "iMac Product Environmental Report", sections: [{ path: "", level: 0, kind: "section" }] },
  gep: {
    title: "Global Economic Prospects, June 2026",
    sections: [
      { path: "Executive Summary", level: 1, kind: "front" },
      { path: "Ch. 1 Global Outlook", level: 1, kind: "section" },
      { path: "Ch. 1 Global Outlook › BOX 1.1 How much will AI affect global growth?", level: 2, kind: "box" },
      { path: CH2, level: 1, kind: "section" },
      { path: `${CH2} › South Asia`, level: 2, kind: "region" },
      { path: `${CH2} › East Asia and Pacific`, level: 2, kind: "region" },
      { path: "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs", level: 1, kind: "section" },
      { path: "Statistical Annex", level: 1, kind: "annex" },
    ],
  },
};

describe("routeQuestion", () => {
  it("filters region questions to that region only", () => {
    expect(routeQuestion("What is the growth outlook for South Asia?", "all", catalog)).toEqual({ docs: ["gep"], paths: [`${CH2} › South Asia`] });
    expect(routeQuestion("How is EAP doing?", "gep", catalog).paths).toEqual([`${CH2} › East Asia and Pacific`]);
  });

  it("keeps only the most specific section when a chapter and a region are both named", () => {
    expect(routeQuestion("What does Chapter 2 say about South Asia?", "all", catalog).paths).toEqual([`${CH2} › South Asia`]);
  });

  it("maps chapter, box, executive summary and annex mentions to their sections", () => {
    expect(routeQuestion("What does Chapter 3 say about sovereign debt?", "gep", catalog).paths).toEqual([
      "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs",
    ]);
    expect(routeQuestion("Summarize Box 1.1", "gep", catalog).paths).toEqual(["Ch. 1 Global Outlook › BOX 1.1 How much will AI affect global growth?"]);
    expect(routeQuestion("According to the executive summary, what are the main risks?", "gep", catalog).paths).toEqual(["Executive Summary"]);
    expect(routeQuestion("Statistical annex forecast for India", "gep", catalog).paths).toEqual(["Statistical Annex"]);
  });

  it("picks documents from keywords when the scope is all, else searches everything", () => {
    expect(routeQuestion("How many depository libraries did GPO visit?", "all", catalog)).toEqual({ docs: ["gpo"], paths: [] });
    expect(routeQuestion("How much less energy does iMac use than ENERGY STAR?", "all", catalog).docs).toEqual(["imac"]);
    expect(routeQuestion("What changed this year?", "all", catalog).docs).toEqual(["gpo", "imac", "gep"]);
  });

  it("scopeRoute searches the scope's documents with no section filters (the noRouter ablation)", () => {
    expect(scopeRoute("gep", catalog)).toEqual({ docs: ["gep"], paths: [] });
    expect(scopeRoute("all", catalog)).toEqual({ docs: ["gpo", "imac", "gep"], paths: [] });
  });

  it("an explicit scope wins and never adds GEP filters to other documents", () => {
    expect(routeQuestion("South Asia growth", "gpo", catalog)).toEqual({ docs: ["gpo"], paths: [] });
  });
});
```

Replace the whole of `web/lib/rag/context.test.ts` with:

```ts
import { describe, expect, it } from "vitest";
import type { Pool } from "pg";
import { buildBlocks, formatSources, loadNeighbours, sectionLabel } from "@/lib/rag/context";
import { candidate } from "@/lib/rag/testing";

describe("buildBlocks", () => {
  it("adds the neighbouring text chunks and labels each block", () => {
    const neighbours = new Map([
      ["10:4", { id: 40, section_id: 10, seq: 4, content: "before", page_start: 95, page_end: 95 }],
      ["10:6", { id: 60, section_id: 10, seq: 6, content: "after", page_start: 96, page_end: 97 }],
    ]);
    const [block] = buildBlocks([candidate(5, { content: "middle" })], neighbours);
    expect(block.text).toBe("before\n\nmiddle\n\nafter");
    expect(block.label).toBe("[S1] Global Economic Prospects › Ch. 2 Regional Outlooks › South Asia | p.95-97 | text");
    expect(block.chunkIds).toEqual([40, 5, 60]);
  });

  it("appends footnotes, skips neighbours already picked and stays within the budget", () => {
    const picked = [
      candidate(5, { content: "a".repeat(30), footnotes: [{ n: 18, text: "ENERGY STAR rules." }] }),
      candidate(6, { content: "b".repeat(30) }),
      candidate(9, { content: "c".repeat(30) }),
    ];
    const neighbours = new Map([["10:6", { id: 6, section_id: 10, seq: 6, content: "dup", page_start: 96, page_end: 96 }]]);
    const blocks = buildBlocks(picked, neighbours, 100);
    expect(blocks.map((b) => b.chunkIds)).toEqual([[5], [6]]);
    expect(blocks[0].text).toContain("Footnotes:\n18. ENERGY STAR rules.");
    expect(formatSources(blocks)).toContain("\n\n---\n\n[S2]");
  });
});

describe("buildBlocks without footnotes", () => {
  it("leaves footnotes out when asked (the noFootnotes ablation)", () => {
    const [block] = buildBlocks([candidate(5, { content: "text", footnotes: [{ n: 18, text: "note" }] })], new Map(), undefined, { footnotes: false });
    expect(block.text).toBe("text");
  });
});

describe("sectionLabel", () => {
  it("names the document alone for text outside any section", () => {
    expect(sectionLabel("iMac Report", "")).toBe("iMac Report");
    expect(sectionLabel("GEP", "Ch. 3")).toBe("GEP › Ch. 3");
    const [block] = buildBlocks([candidate(5, { docTitle: "iMac Report", sectionPath: "" })], new Map());
    expect(block.label).toBe("[S1] iMac Report | p.96 | text");
  });
});

describe("neighbour expansion", () => {
  it("loads neighbours only for GEP text chunks", async () => {
    const seen: unknown[][] = [];
    const pool = { query: async (_sql: string, params: unknown[]) => (seen.push(params), { rows: [] }) } as unknown as Pool;
    await loadNeighbours(pool, [candidate(1, { docSlug: "gpo" }), candidate(2, { docSlug: "imac" })]);
    expect(seen).toEqual([]);
    await loadNeighbours(pool, [candidate(1, { docSlug: "gpo" }), candidate(3, { sectionId: 12, seq: 7 })]);
    expect(seen).toEqual([[[12], [7]]]);
  });

  it("drops a pick's neighbours before dropping the pick itself", () => {
    const neighbours = new Map([["10:5", { id: 50, section_id: 10, seq: 5, content: "n".repeat(60), page_start: 96, page_end: 96 }]]);
    const blocks = buildBlocks([candidate(1, { content: "a".repeat(30) }), candidate(6, { content: "b".repeat(30) })], neighbours, 100);
    expect(blocks.map((b) => b.chunkIds)).toEqual([[1], [6]]);
  });
});
```

Replace the whole of `web/lib/rag/retrieve.int.test.ts` with:

```ts
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/rag; npm run test:int -- lib/rag/retrieve.int.test.ts`
Expected: FAIL. The new tests fail:
- `answer.test.ts`: `keyword` is missing from the search arguments.
- `request.test.ts`: `options` is stripped.
- `router.test.ts`: `scopeRoute is not a function`.
- `context.test.ts`: the footnote is still in the block text.
- The live-DB test: rows still carry a `kwRank`.

- [ ] **Step 3: Write `web/lib/rag/router.ts`.** It adds `scopeRoute`. Replace the whole file with:

```ts
// Deterministic query routing (spec §6.2): pick documents and hard section filters from explicit mentions.
// No LLM call — the reranker judges relevance; this only stops region/chapter questions from mixing sources.

export type Scope = "gpo" | "gep" | "imac" | "all";
export type Catalog = Record<string, { title: string; sections: { path: string; level: number; kind: string }[] }>;
export type Route = { docs: string[]; paths: string[] };

const DOC_HINTS: Record<string, RegExp> = {
  gpo: /\b(gpo|government publishing office|govinfo|fdsys|depository|federal register|congressional record|passports?)\b/i,
  imac: /\b(imac|apple|energy star|carbon footprint|recycled aluminum|packaging)\b/i,
  gep: /\b(world bank|gep|global economic prospects|emdes?|global (growth|outlook|economy)|regional outlooks?|sovereign debt|fiscal policy|commodit\w*|east asia|south asia|sub-saharan|latin america|middle east|central asia)\b/i,
};

export const REGIONS: [RegExp, string][] = [
  [/\b(east asia( and (the )?pacific)?|eap)\b/i, "East Asia and Pacific"],
  [/\b(europe and central asia|eca)\b/i, "Europe and Central Asia"],
  [/\b(latin america( and the caribbean)?|lac)\b/i, "Latin America and the Caribbean"],
  [/\b(middle east|north africa|menaap|mena|mna)\b/i, "Middle East, North Africa, Afghanistan, and Pakistan"],
  [/\b(south asia|sar)\b/i, "South Asia"],
  [/\b(sub-saharan africa|ssa)\b/i, "Sub-Saharan Africa"],
];

/** The scope's documents with no section filters (the noRouter ablation). */
export function scopeRoute(scope: Scope, catalog: Catalog): Route {
  return { docs: scope === "all" ? Object.keys(catalog) : [scope], paths: [] };
}

export function routeQuestion(question: string, scope: Scope, catalog: Catalog): Route {
  const detected = Object.keys(DOC_HINTS).filter((slug) => slug in catalog && DOC_HINTS[slug].test(question));
  let docs = scope !== "all" ? [scope] : detected.length ? detected : Object.keys(catalog);
  const paths: string[] = [];
  const gep = docs.includes("gep") ? catalog.gep?.sections ?? [] : [];
  const has = (path: string) => gep.some((section) => section.path === path);
  const chapter = question.match(/\bchapter\s+(\d)\b/i);
  if (chapter) {
    const found = gep.find((section) => section.level === 1 && section.path.startsWith(`Ch. ${chapter[1]} `));
    if (found) paths.push(found.path);
  }
  const box = question.match(/\bbox\s+(\d+\.\d+)\b/i);
  if (box) gep.filter((s) => s.kind === "box" && s.path.includes(`BOX ${box[1]} `)).forEach((s) => paths.push(s.path));
  if (/\bexecutive summary\b/i.test(question) && has("Executive Summary")) paths.push("Executive Summary");
  if (/\bstatistical (annex|appendix)\b/i.test(question) && has("Statistical Annex")) paths.push("Statistical Annex");
  for (const [pattern, region] of REGIONS) {
    const path = `Ch. 2 Regional Outlooks › ${region}`;
    if (pattern.test(question) && has(path)) paths.push(path);
  }
  if (paths.length && scope === "all") docs = ["gep"];
  // "Chapter 2 … South Asia": the region alone, or the chapter filter would let every region back in
  const specific = [...new Set(paths)].filter((p) => !paths.some((other) => other.startsWith(`${p} › `)));
  return { docs, paths: specific };
}
```

- [ ] **Step 4: Write `web/lib/rag/retrieve.ts`.** It adds `SearchArgs.keyword`; a NULL keyword query matches no rows. Replace the whole file with:

```ts
import type { Pool } from "pg";
import type { Catalog } from "@/lib/rag/router";

export type Candidate = {
  id: number;
  docSlug: string;
  docTitle: string;
  sectionId: number;
  sectionPath: string;
  seq: number;
  kind: string;
  content: string;
  pageStart: number;
  pageEnd: number;
  footnotes: { n: number; text: string }[];
  score: number; // reciprocal rank fusion
  vecRank: number | null;
  kwRank: number | null;
};

export type SearchArgs = {
  vector: number[]; question: string; docs: string[]; paths: string[]; limit?: number;
  keyword?: boolean; // false: vector search only (the vectorOnly ablation)
};

/** Keyword side of hybrid search: any question word may match (OR), ranked by coverage. */
export function keywordQuery(question: string): string {
  const words = question.toLowerCase().match(/[a-z0-9][a-z0-9.%-]*[a-z0-9%]|\d/g) ?? [];
  return [...new Set(words.filter((word) => word.length >= 3 || /\d/.test(word)))].join(" or ");
}

// Vector top 40 ∪ keyword top 40 inside the routed documents/sections, fused with RRF (k = 60). Spec §6.3.
const HYBRID_SQL = `
WITH q AS (SELECT $1::vector AS v, websearch_to_tsquery('english', $2) AS ts),
scoped AS (
  SELECT c.id, c.embedding, c.tsv FROM chunks c
  JOIN documents d ON d.id = c.doc_id JOIN sections s ON s.id = c.section_id
  WHERE d.slug = ANY($3::text[])
    AND ($4::text[] IS NULL OR s.path = ANY($4::text[])
         OR EXISTS (SELECT 1 FROM unnest($4::text[]) AS p WHERE starts_with(s.path, p || ' › ')))
),
vec AS (
  SELECT id, row_number() OVER (ORDER BY embedding <=> (SELECT v FROM q)) AS r
  FROM scoped ORDER BY embedding <=> (SELECT v FROM q) LIMIT 40
),
kw AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, (SELECT ts FROM q)) DESC) AS r
  FROM scoped WHERE tsv @@ (SELECT ts FROM q) ORDER BY ts_rank_cd(tsv, (SELECT ts FROM q)) DESC LIMIT 40
),
fused AS (SELECT id, sum(1.0 / (60 + r)) AS score FROM (SELECT * FROM vec UNION ALL SELECT * FROM kw) u GROUP BY id)
SELECT c.id, d.slug AS doc_slug, d.title AS doc_title, s.id AS section_id, s.path AS section_path, c.seq, c.kind,
       c.content, c.page_start, c.page_end, c.footnotes, f.score, v.r AS vec_rank, k.r AS kw_rank
FROM fused f
JOIN chunks c ON c.id = f.id JOIN sections s ON s.id = c.section_id JOIN documents d ON d.id = c.doc_id
LEFT JOIN vec v ON v.id = f.id LEFT JOIN kw k ON k.id = f.id
ORDER BY f.score DESC LIMIT $5`;

type Row = {
  id: number; doc_slug: string; doc_title: string; section_id: number; section_path: string; seq: number; kind: string;
  content: string; page_start: number; page_end: number; footnotes: { n: number; text: string }[]; score: string;
  vec_rank: string | null; kw_rank: string | null;
};

export function toCandidate(row: Row): Candidate {
  return {
    id: row.id, docSlug: row.doc_slug, docTitle: row.doc_title, sectionId: row.section_id, sectionPath: row.section_path,
    seq: row.seq, kind: row.kind, content: row.content, pageStart: row.page_start, pageEnd: row.page_end,
    footnotes: row.footnotes ?? [], score: Number(row.score),
    vecRank: row.vec_rank === null ? null : Number(row.vec_rank), kwRank: row.kw_rank === null ? null : Number(row.kw_rank),
  };
}

export async function hybridSearch(pool: Pool, { vector, question, docs, paths, limit = 20, keyword = true }: SearchArgs): Promise<Candidate[]> {
  const { rows } = await pool.query<Row>(HYBRID_SQL, [
    `[${vector.join(",")}]`, keyword ? keywordQuery(question) : null, docs, paths.length ? paths : null, limit, // NULL query: no keyword rows
  ]);
  return rows.map(toCandidate);
}

export async function loadCatalog(pool: Pool): Promise<Catalog> {
  const { rows } = await pool.query<{ slug: string; title: string; path: string; level: number; kind: string }>(
    `SELECT d.slug, d.title, s.path, s.level, s.kind FROM sections s JOIN documents d ON d.id = s.doc_id ORDER BY d.slug, s.id`,
  );
  const catalog: Catalog = {};
  for (const row of rows) {
    catalog[row.slug] ??= { title: row.title, sections: [] };
    catalog[row.slug].sections.push({ path: row.path, level: row.level, kind: row.kind });
  }
  return catalog;
}
```

- [ ] **Step 5: Write `web/lib/rag/context.ts`.** It adds the `footnotes` flag to `buildBlocks`. Replace the whole file with:

```ts
import type { Pool } from "pg";
import type { Candidate } from "@/lib/rag/retrieve";

export const CONTEXT_CHARS = 24_000; // ≈ 6k tokens of sources (spec §6.5)

export type SourceBlock = {
  id: number; // S1, S2, … in the prompt
  label: string;
  text: string;
  docSlug: string;
  sectionPath: string;
  pageStart: number;
  pageEnd: number;
  kind: string;
  chunkIds: number[];
};

type Neighbour = { id: number; section_id: number; seq: number; content: string; page_start: number; page_end: number };

/** GEP text chunks next to each picked GEP text chunk in the same section (seq ± 1), keyed by "section:seq".
 * Only GEP (spec §6.5): its child chunks split long sections; GPO chunks already overlap. */
export async function loadNeighbours(pool: Pool, picked: Candidate[]): Promise<Map<string, Neighbour>> {
  const texts = picked.filter((c) => c.kind === "text" && c.docSlug === "gep");
  if (!texts.length) return new Map();
  const { rows } = await pool.query<Neighbour>(
    `SELECT c.id, c.section_id, c.seq, c.content, c.page_start, c.page_end
     FROM chunks c JOIN unnest($1::int[], $2::int[]) AS p(section_id, seq) ON c.section_id = p.section_id
     WHERE c.kind = 'text' AND c.seq IN (p.seq - 1, p.seq + 1)`,
    [texts.map((c) => c.sectionId), texts.map((c) => c.seq)],
  );
  return new Map(rows.map((row) => [`${row.section_id}:${row.seq}`, row]));
}

/** "Doc › Section", or the document alone for text before the first heading (empty path). */
export function sectionLabel(docTitle: string, sectionPath: string): string {
  return sectionPath ? `${docTitle} › ${sectionPath}` : docTitle;
}

export function buildBlocks(
  picked: Candidate[], neighbours: Map<string, Neighbour>, budget = CONTEXT_CHARS, { footnotes: withFootnotes = true } = {},
): SourceBlock[] {
  const used = new Set(picked.map((c) => c.id));
  const blocks: SourceBlock[] = [];
  let size = 0;
  for (const c of picked) {
    const own = { content: c.content, id: c.id, start: c.pageStart, end: c.pageEnd };
    const around = (seq: number) => {
      const n = c.kind === "text" ? neighbours.get(`${c.sectionId}:${seq}`) : undefined;
      return n && !used.has(n.id) ? [{ content: n.content, id: n.id, start: n.page_start, end: n.page_end }] : [];
    };
    const footnotes = withFootnotes ? c.footnotes.map((f) => `${f.n}. ${f.text}`).join("\n") : "";
    const render = (ps: (typeof own)[]) => ps.map((p) => p.content).join("\n\n") + (footnotes ? `\n\nFootnotes:\n${footnotes}` : "");
    let parts = [...around(c.seq - 1), own, ...around(c.seq + 1)];
    let text = render(parts);
    if (blocks.length && size + text.length > budget) {
      parts = [own]; // drop the neighbours before dropping a scored pick
      text = render(parts);
      if (size + text.length > budget) break; // keep the best-ranked blocks within budget
    }
    parts.forEach((p) => used.add(p.id));
    size += text.length;
    const pageStart = Math.min(...parts.map((p) => p.start));
    const pageEnd = Math.max(...parts.map((p) => p.end));
    const pages = pageStart === pageEnd ? `p.${pageStart}` : `p.${pageStart}-${pageEnd}`;
    const id = blocks.length + 1;
    blocks.push({
      id, label: `[S${id}] ${sectionLabel(c.docTitle, c.sectionPath)} | ${pages} | ${c.kind}`, text,
      docSlug: c.docSlug, sectionPath: c.sectionPath, pageStart, pageEnd, kind: c.kind, chunkIds: parts.map((p) => p.id),
    });
  }
  return blocks;
}

export function formatSources(blocks: SourceBlock[]): string {
  return blocks.map((b) => `${b.label}\n${b.text}`).join("\n\n---\n\n");
}
```

- [ ] **Step 6: Write `web/lib/rag/answer.ts`.** It adds `Options` and applies the four switches. Replace the whole file with:

```ts
import { errorMessage } from "@/lib/rag/health";
import { buildBlocks, formatSources, sectionLabel, type SourceBlock } from "@/lib/rag/context";
import { SYSTEM_PROMPT, userPrompt } from "@/lib/rag/prompt";
import { KEEP, selectRelevant } from "@/lib/rag/rerank";
import type { Candidate, SearchArgs } from "@/lib/rag/retrieve";
import { routeQuestion, scopeRoute, type Catalog, type Route, type Scope } from "@/lib/rag/router";
import { verifyAnswer } from "@/lib/rag/verify";

export type Turn = { role: "user" | "assistant"; content: string };
/** Ablation switches (spec §8.3), accepted only outside production: each turns off one technique. */
export type Options = { vectorOnly?: boolean; noRerank?: boolean; noRouter?: boolean; noFootnotes?: boolean };
export type ChatRequest = { question: string; scope: Scope; history: Turn[]; options?: Options };
export type SourceInfo = {
  id: number; slug: string; doc: string; sectionPath: string; pageStart: number; pageEnd: number; kind: string; snippet: string;
  text: string; // the whole block the model saw, for the sources drawer
};
export type Debug = {
  question: string;
  route: Route;
  candidates: { chunkId: number; vecRank: number | null; kwRank: number | null; rrf: number; rerank: number | null }[];
};
export type ChatEvent =
  | { type: "sources"; sources: SourceInfo[] }
  | { type: "text"; delta: string }
  | { type: "done"; warnings: string[]; debug: Debug }
  | { type: "error"; message: string };

export type Deps = {
  catalog(): Promise<Catalog>;
  condense(history: Turn[], question: string): Promise<string>;
  embed(question: string): Promise<number[]>;
  search(args: SearchArgs): Promise<Candidate[]>;
  rerank(question: string, candidates: Candidate[]): Promise<Map<number, number>>;
  neighbours(picked: Candidate[]): Promise<Parameters<typeof buildBlocks>[1]>;
  generate(system: string, prompt: string): AsyncIterable<string>;
};

export function refusal(scope: Scope, catalog: Catalog, candidates: Candidate[]): string {
  const where = scope === "all" ? "the documents" : catalog[scope]?.title ?? scope;
  const closest = [...new Set(candidates.map((c) => sectionLabel(c.docTitle, c.sectionPath)))].slice(0, 3);
  return `I couldn't find this in ${where}.` + (closest.length ? ` Closest sections: ${closest.join("; ")}.` : "");
}

function sourceInfo(block: SourceBlock, docTitle: string): SourceInfo {
  return {
    id: block.id, slug: block.docSlug, doc: docTitle, sectionPath: block.sectionPath, pageStart: block.pageStart,
    pageEnd: block.pageEnd, kind: block.kind, snippet: block.text.slice(0, 300), text: block.text,
  };
}

/** The whole question → answer flow (spec §6), emitting stream events. Every external call comes from `deps`. */
export async function answer(request: ChatRequest, deps: Deps, emit: (event: ChatEvent) => void): Promise<void> {
  const warnings: string[] = [];
  const question = await deps.condense(request.history, request.question);
  const catalog = await deps.catalog();
  const options = request.options ?? {};
  const route = options.noRouter ? scopeRoute(request.scope, catalog) : routeQuestion(question, request.scope, catalog);
  const vector = await deps.embed(question);
  const candidates = await deps.search({ vector, question, docs: route.docs, paths: route.paths, keyword: !options.vectorOnly });
  let scores: Map<number, number> | null = null;
  if (candidates.length && !options.noRerank) {
    try {
      scores = await deps.rerank(question, candidates);
    } catch {
      warnings.push("reranking failed; using search order");
    }
  }
  const picked = scores ? selectRelevant(candidates, scores) : candidates.slice(0, KEEP);
  const debug: Debug = {
    question, route,
    candidates: candidates.map((c) => ({ chunkId: c.id, vecRank: c.vecRank, kwRank: c.kwRank, rrf: c.score, rerank: scores?.get(c.id) ?? null })),
  };
  if (!picked.length) {
    emit({ type: "sources", sources: [] });
    emit({ type: "text", delta: refusal(request.scope, catalog, candidates) });
    emit({ type: "done", warnings, debug });
    return;
  }
  const blocks = buildBlocks(picked, await deps.neighbours(picked), undefined, { footnotes: !options.noFootnotes });
  const titles = new Map(picked.map((c) => [c.docSlug, c.docTitle]));
  emit({ type: "sources", sources: blocks.map((b) => sourceInfo(b, titles.get(b.docSlug) ?? b.docSlug)) });
  let text = "";
  try {
    for await (const delta of deps.generate(SYSTEM_PROMPT, userPrompt(question, formatSources(blocks)))) {
      text += delta;
      emit({ type: "text", delta });
    }
  } catch (error) {
    emit({ type: "error", message: `answer generation failed: ${errorMessage(error)}` });
    return;
  }
  warnings.push(...verifyAnswer(text, blocks));
  emit({ type: "done", warnings, debug });
}
```

- [ ] **Step 7: Write `web/lib/rag/request.ts`.** It accepts `options` and drops them in production. Replace the whole file with:

```ts
import { z } from "zod";
import type { ChatRequest } from "@/lib/rag/answer";

const ChatRequestSchema = z.object({
  question: z.string().trim().min(1).max(1000),
  scope: z.enum(["gpo", "gep", "imac", "all"]).default("all"),
  history: z
    .array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().max(20_000) }))
    .max(100)
    .default([])
    .transform((turns) => turns.slice(-6)), // spec §6.1: the last 6 turns
  options: z
    .strictObject({ vectorOnly: z.boolean(), noRerank: z.boolean(), noRouter: z.boolean(), noFootnotes: z.boolean() })
    .partial()
    .optional(),
});

export function parseChatRequest(body: unknown): { ok: true; request: ChatRequest } | { ok: false; error: string } {
  const result = ChatRequestSchema.safeParse(body);
  if (!result.success) {
    return { ok: false, error: result.error.issues.map((i) => `${i.path.join(".") || "body"}: ${i.message}`).join("; ") };
  }
  const { options, ...request } = result.data;
  // Ablation switches are for the evaluation (spec §6): never honoured in production.
  return { ok: true, request: process.env.NODE_ENV === "production" || !options ? request : { ...request, options } };
}
```

- [ ] **Step 8: Write `web/lib/rag/deps.ts`.** It adds the `chatModel` override. Replace the whole file with:

```ts
import { getEnv, utilityModel } from "@/lib/env";
import type { Deps } from "@/lib/rag/answer";
import { loadNeighbours } from "@/lib/rag/context";
import { getPool } from "@/lib/rag/db";
import { embedQuery, getGenAI } from "@/lib/rag/gemini";
import { generateText, streamText, type GenClient } from "@/lib/rag/genai";
import { condensePrompt } from "@/lib/rag/prompt";
import { rerank } from "@/lib/rag/rerank";
import { hybridSearch, loadCatalog } from "@/lib/rag/retrieve";
import type { Catalog } from "@/lib/rag/router";

/** Load the catalog once; a failed or empty load (DB down, nothing ingested yet) is retried on the next request. */
export function memoCatalog(load: () => Promise<Catalog>): () => Promise<Catalog> {
  let cached: Promise<Catalog> | undefined;
  return () => {
    const pending = (cached ??= load());
    pending.then((c) => { if (!Object.keys(c).length) cached = undefined; }, () => { cached = undefined; });
    return pending;
  };
}

const catalog = memoCatalog(() => loadCatalog(getPool()));

/**
 * Production wiring: Postgres, Gemini embeddings, the utility model for helpers and the chat model for answers.
 * `signal` (the client went away) cancels the Gemini calls of this request; `chatModel` overrides the answer model
 * (the evaluation's ablation runs answer with the utility model).
 */
export function productionDeps(signal?: AbortSignal, { chatModel }: { chatModel?: string } = {}): Deps {
  const env = getEnv();
  const client = getGenAI() as unknown as GenClient;
  const helper = utilityModel(env);
  return {
    catalog,
    condense: async (history, question) => {
      if (!history.length) return question;
      try {
        return await generateText(client, helper, condensePrompt(history, question), { signal });
      } catch {
        return question; // a failed rewrite should not block the answer
      }
    },
    embed: embedQuery,
    search: (args) => hybridSearch(getPool(), args),
    rerank: (question, candidates) => rerank(client, helper, question, candidates, signal),
    neighbours: (picked) => loadNeighbours(getPool(), picked),
    generate: (system, prompt) => streamText(client, chatModel ?? env.GEMINI_CHAT_MODEL, system, prompt, { signal }),
  };
}
```

- [ ] **Step 9: Run the tests and the type check**

Run: `cd web && npm test && npm run test:int && npx next typegen && npx tsc --noEmit`
Expected: 95 passed, 6 skipped; then 100 passed, 1 skipped; no type errors

- [ ] **Step 10: Commit**

```bash
git add web/lib/rag/answer.ts web/lib/rag/router.ts web/lib/rag/retrieve.ts web/lib/rag/context.ts web/lib/rag/request.ts web/lib/rag/deps.ts web/lib/rag/answer.test.ts web/lib/rag/request.test.ts web/lib/rag/router.test.ts web/lib/rag/context.test.ts web/lib/rag/retrieve.int.test.ts
git commit -m "Add ablation switches to the pipeline"
```

---

### Task 2: Question set

**Files:**
- Create: `eval/questions.yaml`, `web/lib/eval/questions.ts`
- Modify: `web/package.json`, `web/package-lock.json` (the `yaml` dev dependency)
- Test: `web/lib/eval/questions.test.ts`

**Interfaces:**
- Produces: `EvalQuestion` (`id`, `type`, `scope`, `question`, `must_contain[]`, `must_not_contain[]`, `expected_pages: number[]` with ranges expanded, `expected_section?`, `rubric?[]`); `parseQuestions(yamlText)`, `loadQuestions(file)`

- [ ] **Step 1: Install the YAML parser**

Run: `cd web && npm install -D yaml@2.8.1`
Expected: `package.json` lists `"yaml": "^2.8.1"` under `devDependencies`

- [ ] **Step 2: Write the failing test.** Create `web/lib/eval/questions.test.ts`:

```ts
import path from "node:path";
import { describe, expect, it } from "vitest";
import { loadQuestions, parseQuestions } from "@/lib/eval/questions";

const FILE = path.resolve(__dirname, "../../../eval/questions.yaml");

describe("eval/questions.yaml", () => {
  const questions = loadQuestions(FILE);

  it("holds the spec's 18 questions: 9 brief, 4 coverage, 4 trap, 1 isolation", () => {
    const count = (type: string) => questions.filter((q) => q.type === type).length;
    expect([questions.length, count("brief"), count("coverage"), count("trap"), count("isolation")]).toEqual([18, 9, 4, 4, 1]);
  });

  it("gives every brief question's document three questions and the two broad GEP questions a rubric", () => {
    const brief = questions.filter((q) => q.type === "brief");
    expect(["gpo", "gep", "imac"].map((s) => brief.filter((q) => q.scope === s).length)).toEqual([3, 3, 3]);
    expect(questions.filter((q) => q.rubric).map((q) => q.id)).toEqual(["gep-chapter-3", "gep-exec-risks"]);
  });

  it("expands page ranges", () => {
    expect(questions.find((q) => q.id === "gep-south-asia")?.expected_pages).toEqual([96, 97, 98]);
  });
});

describe("parseQuestions", () => {
  it("rejects answerable questions without facts, unknown fields and duplicate ids", () => {
    expect(() => parseQuestions("- {id: a, type: brief, scope: gpo, question: q}")).toThrow("need must_contain");
    expect(() => parseQuestions("- {id: a, type: trap, scope: gpo, question: q, answer: x}")).toThrow();
    expect(() => parseQuestions("- {id: a, type: trap, scope: gpo, question: q}\n- {id: a, type: trap, scope: gpo, question: q}")).toThrow("duplicate question id: a");
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd web && npm test -- lib/eval`
Expected: FAIL with `Cannot find package '@/lib/eval/questions'`

- [ ] **Step 4: Write `eval/questions.yaml`.** Every value below was checked against the named PDF pages:
- 166 libraries (p5, p21)
- govinfo full-scale deployment in FY 2018 (p4)
- $375 million (p5) and $375.7 million (p14, p17)
- "Growth in SAR is expected to soften to 6.3 percent in 2026" (pp96–98)
- Chapter 3 summary (p109, also p19)
- Executive Summary risks (p19)
- 58 percent (p7)
- 346 kg CO2e: production 50%, use 45% (p10)
- 100 percent recycled aluminum in the stand (pp1, 2, 4)
- 0.59W sleep at 230V (p12)
- Bhutan 2026f 7.1 (p189)
- BOX 1.1 "strongest period of global growth since the 1970s" (p30)
- EAP 4.2 percent (pp81–83)

Create the file with:

```yaml
# Evaluation set (spec §8.1). Every must_contain / must_not_contain / rubric value was copied from the source PDF
# pages named in expected_pages, not written from memory. Pages are PDF page numbers (1-based), as stored in chunks;
# "107-138" is an inclusive range.

- id: gpo-libraries
  type: brief
  scope: gpo
  question: How many depository libraries did GPO staff visit in FY 2018?
  must_contain: ["166"]
  expected_pages: [5, 21]

- id: gpo-govinfo
  type: brief
  scope: gpo
  question: What new digital system replaced FDsys, and when was it fully deployed?
  must_contain: ["govinfo", "2018"]
  expected_pages: [4]

- id: gpo-procurement
  type: brief
  scope: gpo
  question: How much did GPO procure in printing and information products in FY 2018 on behalf of federal agencies?
  must_contain: ["375"]
  expected_pages: [5, 14, 17]

- id: gep-south-asia
  type: brief
  scope: gep
  question: What is the growth outlook for South Asia in this report?
  must_contain: ["6.3%"]
  expected_pages: ["96-98"]
  expected_section: Ch. 2 Regional Outlooks › South Asia

- id: gep-chapter-3
  type: brief
  scope: gep
  question: What does Chapter 3 say about sovereign debt in developing economies?
  must_contain: ["interest rate"]
  expected_pages: ["107-138"]
  expected_section: "Ch. 3 A Rising Challenge: Sovereign Debt Levels and Interest Rates in EMDEs"
  rubric: # p109 (chapter summary), also p19
    - Rising government debt raises interest rates, debt-service payments and the likelihood of debt distress
    - Debt levels are positively associated with sovereign bond spreads and domestic-currency government bond yields
    - The relationship is non-linear - increases in spreads or yields are larger the higher the debt-to-GDP ratio
    - Rising EMDE debt since 2010 is associated with spreads and yields about 110 and 30 basis points higher
    - Rising debt in advanced economies has added to EMDE interest rates
    - Effects are larger with a history of default, low credit ratings, frontier status, short-term debt or weak governance
    - Strengthening fiscal positions (revenue mobilization, efficient spending, debt management) is recommended

- id: gep-exec-risks
  type: brief
  scope: gep
  question: According to the executive summary, what are the main risks to the global outlook?
  must_contain: ["downside"]
  expected_pages: [19, 20]
  expected_section: Executive Summary
  rubric: # p19
    - Risks are skewed to the downside
    - Renewed escalation of hostilities or prolonged disruptions to commodity flows could raise commodity prices
    - Higher inflationary pressures and food insecurity
    - Financial stress
    - In a severe energy-disruption scenario with financial stress, global growth could fall to 1.3 percent in 2026
    - Persistent trade policy uncertainty, geopolitical strains and weather-related shocks
    - Upside - broader investment in and adoption of AI could lift activity

- id: imac-energy-star
  type: brief
  scope: imac
  question: How much less energy does iMac use compared to the ENERGY STAR requirement?
  must_contain: ["58%"]
  expected_pages: [7]

- id: imac-carbon
  type: brief
  scope: imac
  question: What is the total carbon footprint of the iMac, and how much of that comes from product use versus manufacturing?
  must_contain: ["346", "45%", "50%"]
  expected_pages: [2, 10]

- id: imac-stand
  type: brief
  scope: imac
  question: What material is the iMac stand made from, and what percentage of it is recycled?
  must_contain: ["aluminum", "100"]
  expected_pages: [1, 2, 4]

- id: imac-sleep-230v
  type: coverage
  scope: imac
  question: What is the iMac's power consumption in sleep mode at 230V?
  must_contain: ["0.59"]
  expected_pages: [12]

- id: imac-carbon-split
  type: coverage
  scope: imac
  question: What share of the iMac's carbon footprint comes from production and what share from product use?
  must_contain: ["50%", "45%"]
  expected_pages: [10]

- id: gep-annex-bhutan
  type: coverage
  scope: gep
  question: According to the statistical annex, what is Bhutan's real GDP growth forecast for 2026?
  must_contain: ["7.1"]
  expected_pages: [189]
  expected_section: Statistical Annex

- id: gep-box-ai
  type: coverage
  scope: gep
  question: According to Box 1.1, how could AI affect global growth in the 2030s under optimistic assumptions?
  must_contain: ["1970s"]
  expected_pages: ["30-35"]
  expected_section: "Ch. 1 Global Outlook › BOX 1.1 How much will AI affect global growth?"

- id: trap-imac-battery
  type: trap
  scope: imac
  question: What is the battery life of the iMac?

- id: trap-gpo-2022
  type: trap
  scope: gpo
  question: What was GPO's budget in FY 2022?

- id: trap-gep-antarctica
  type: trap
  scope: gep
  question: What is the growth outlook for Antarctica?

- id: trap-world-cup
  type: trap
  scope: all
  question: Who won the 2022 FIFA World Cup?

- id: isolation-eap
  type: isolation
  scope: gep
  question: What is the growth outlook for East Asia and Pacific?
  must_contain: ["4.2%"]
  must_not_contain: ["6.3%", "6.6%"] # South Asia-only figures (pp96-98), absent from the EAP pages 81-83
  expected_pages: ["81-83"]
  expected_section: Ch. 2 Regional Outlooks › East Asia and Pacific
```

- [ ] **Step 5: Write `web/lib/eval/questions.ts`**

```ts
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { z } from "zod";

// One evaluation question (spec §8.1). Answerable types carry the facts the answer must state and where they live.
const PageList = z
  .array(z.union([z.number().int().positive(), z.string().regex(/^\d+-\d+$/, 'a page or a "from-to" range')]))
  .transform((items) =>
    items.flatMap((item) => {
      if (typeof item === "number") return [item];
      const [from, to] = item.split("-").map(Number);
      return Array.from({ length: to - from + 1 }, (_, i) => from + i);
    }),
  );

const Question = z
  .strictObject({
    id: z.string().regex(/^[a-z0-9-]+$/),
    type: z.enum(["brief", "coverage", "trap", "isolation"]),
    scope: z.enum(["gpo", "gep", "imac", "all"]),
    question: z.string().min(1).max(1000),
    must_contain: z.array(z.string().min(1)).default([]),
    must_not_contain: z.array(z.string().min(1)).default([]),
    expected_pages: PageList.default([]),
    expected_section: z.string().optional(),
    rubric: z.array(z.string().min(1)).optional(),
  })
  .refine((q) => q.type === "trap" || (q.must_contain.length > 0 && q.expected_pages.length > 0), {
    message: "answerable questions need must_contain and expected_pages",
  });

export type EvalQuestion = z.output<typeof Question>;

export function parseQuestions(yamlText: string): EvalQuestion[] {
  const questions = z.array(Question).parse(parse(yamlText));
  const seen = new Set<string>();
  for (const q of questions) {
    if (seen.has(q.id)) throw new Error(`duplicate question id: ${q.id}`);
    seen.add(q.id);
  }
  return questions;
}

export function loadQuestions(file: string): EvalQuestion[] {
  return parseQuestions(readFileSync(file, "utf8"));
}
```

- [ ] **Step 6: Run the tests**

Run: `cd web && npm test`
Expected: 99 passed, 6 skipped

- [ ] **Step 7: Commit**

```bash
git add eval/questions.yaml web/lib/eval/questions.ts web/lib/eval/questions.test.ts web/package.json web/package-lock.json
git commit -m "Add evaluation question set"
```

---

### Task 3: Scoring

**Files:**
- Create: `web/lib/eval/score.ts`
- Test: `web/lib/eval/score.test.ts`

**Interfaces:**
- Consumes: `EvalQuestion` (Task 2), `SourceInfo` (sub-project 7).
- Produces:
  - `Outcome = { answer; sources: SourceInfo[]; warnings: string[]; error? }`
  - `Score = { hit; facts; citation: number | null; verifier; refused; isolated; pass }`
  - `normalize(text)`, `citedIds(answer)`, `relevant(source, q)`, `scoreAnswer(q, outcome): Score`

- [ ] **Step 1: Write the failing test.** Create `web/lib/eval/score.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { EvalQuestion } from "@/lib/eval/questions";
import { citedIds, normalize, scoreAnswer } from "@/lib/eval/score";
import type { SourceInfo } from "@/lib/rag/answer";

const SAR = "Ch. 2 Regional Outlooks › South Asia";
const EAP = "Ch. 2 Regional Outlooks › East Asia and Pacific";
const question = (q: Partial<EvalQuestion>): EvalQuestion => ({
  id: "q", type: "brief", scope: "gep", question: "?", must_contain: [], must_not_contain: [], expected_pages: [], ...q,
});
const source = (id: number, sectionPath: string, pageStart: number, pageEnd = pageStart): SourceInfo => ({
  id, slug: "gep", doc: "GEP", sectionPath, pageStart, pageEnd, kind: "text", snippet: "", text: "",
});

describe("normalize", () => {
  it("matches figures however they are written", () => {
    expect(normalize("Growth softens to 6.3 Percent; 802,834 copies – about 58 per cent")).toBe("growth softens to 6.3%; 802834 copies - about 58%");
    expect(citedIds("a [S1][S3] b [S2, S4] c [S5; 6]")).toEqual([1, 3, 2, 4, 5, 6]);
  });
});

describe("scoreAnswer", () => {
  const sar = question({ must_contain: ["6.3%"], expected_pages: [96, 97, 98], expected_section: SAR });

  it("passes a correct, cited answer and measures citation precision", () => {
    const sources = [source(1, `${SAR} › Outlook`, 96), source(2, "Ch. 1 Global Outlook", 30)];
    const score = scoreAnswer(sar, { answer: "Growth is 6.3 percent [S1], below 2025 [S2].", sources, warnings: [] });
    expect(score).toEqual({ hit: true, facts: true, citation: 0.5, verifier: true, refused: null, isolated: null, pass: true });
  });

  it("fails a missing fact, an answer without relevant citations, and an error", () => {
    const sources = [source(1, `${SAR} › Outlook`, 96), source(2, "Ch. 1 Global Outlook", 30)];
    expect(scoreAnswer(sar, { answer: "Growth softens [S1].", sources, warnings: [] }).pass).toBe(false);
    expect(scoreAnswer(sar, { answer: "Growth is 6.3 percent [S2].", sources, warnings: [] })).toMatchObject({ citation: 0, pass: false });
    expect(scoreAnswer(sar, { answer: "", sources, warnings: [], error: "503" })).toMatchObject({ facts: false, verifier: null, pass: false });
  });

  it("reports verifier warnings but ignores the rerank fallback notice", () => {
    const sources = [source(1, SAR, 96)];
    expect(scoreAnswer(sar, { answer: "6.3 percent [S1]", sources, warnings: ["unverified figures: 7"] }).verifier).toBe(false);
    expect(scoreAnswer(sar, { answer: "6.3 percent [S1]", sources, warnings: ["reranking failed; using search order"] }).verifier).toBe(true);
  });

  it("passes a trap only on the refusal message", () => {
    const trap = question({ type: "trap", scope: "imac" });
    expect(scoreAnswer(trap, { answer: "I couldn't find this in iMac Report.", sources: [], warnings: [] }).pass).toBe(true);
    expect(scoreAnswer(trap, { answer: "Battery life is 10 hours [S1].", sources: [source(1, "", 3)], warnings: [] })).toMatchObject({ refused: false, pass: false });
  });

  it("fails isolation when another region is cited or its figures appear", () => {
    const eap = question({ type: "isolation", must_contain: ["4.2%"], must_not_contain: ["6.3%"], expected_pages: [81, 82, 83], expected_section: EAP });
    const ok = [source(1, `${EAP} › Outlook`, 81), source(2, "Statistical Annex", 187)];
    expect(scoreAnswer(eap, { answer: "EAP growth is 4.2 percent [S1][S2].", sources: ok, warnings: [] })).toMatchObject({ isolated: true, pass: true });
    expect(scoreAnswer(eap, { answer: "4.2 percent [S1].", sources: [...ok, source(3, SAR, 96)], warnings: [] })).toMatchObject({ isolated: false, pass: false });
    expect(scoreAnswer(eap, { answer: "4.2 percent [S1], vs 6.3 percent [S1].", sources: ok, warnings: [] })).toMatchObject({ isolated: false, pass: false });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- lib/eval/score.test.ts`
Expected: FAIL with `Cannot find package '@/lib/eval/score'`

- [ ] **Step 3: Write `web/lib/eval/score.ts`**

```ts
import type { EvalQuestion } from "@/lib/eval/questions";
import type { SourceInfo } from "@/lib/rag/answer";

// Per-question scorecard (spec §8.2). Each metric is null when it does not apply to the question's type.

export type Outcome = { answer: string; sources: SourceInfo[]; warnings: string[]; error?: string };
export type Score = {
  hit: boolean | null; // an expected page or section among the final sources
  facts: boolean | null; // every must_contain present after normalization
  citation: number | null; // share of cited sources on expected pages/sections
  verifier: boolean | null; // no verifier warnings
  refused: boolean | null; // traps: the fixed refusal message
  isolated: boolean | null; // isolation: only the expected section (or annex rows) cited, no forbidden figures
  pass: boolean;
};

const REFUSAL = "i couldn't find this"; // the gate's fixed message and the prompt's refusal rule both start this way
const CITATION = /\[(S\d+(?:\s*[,;]\s*S?\d+)*)\]/g;

/** Lowercase, unify dashes, drop thousands separators and write "percent" as "%" so "6.3 percent" matches "6.3%". */
export function normalize(text: string): string {
  return text
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[−–—]/g, "-")
    .replace(/(\d),(?=\d{3}\b)/g, "$1")
    .replace(/\s*\b(percent|per cent)\b/g, "%")
    .replace(/\s+%/g, "%")
    .replace(/\s+/g, " ")
    .trim();
}

export function citedIds(answer: string): number[] {
  return [...answer.matchAll(CITATION)].flatMap((m) => m[1].split(/[,;]/).map((id) => Number(id.trim().replace(/^S/, ""))));
}

const inSection = (path: string, section: string | undefined) => !!section && (path === section || path.startsWith(`${section} › `));

/** A source counts as relevant when it sits in the expected section or covers an expected page. */
export function relevant(source: SourceInfo, q: EvalQuestion): boolean {
  return inSection(source.sectionPath, q.expected_section) || q.expected_pages.some((p) => p >= source.pageStart && p <= source.pageEnd);
}

export function scoreAnswer(q: EvalQuestion, outcome: Outcome): Score {
  const { answer, sources, warnings, error } = outcome;
  const text = normalize(answer);
  const verifier = error ? null : warnings.filter((w) => !w.startsWith("reranking failed")).length === 0;
  if (q.type === "trap") {
    const refused = !error && text.startsWith(REFUSAL);
    return { hit: null, facts: null, citation: null, verifier: null, refused, isolated: null, pass: refused };
  }
  const hit = sources.some((s) => relevant(s, q));
  const facts = !error && q.must_contain.every((fact) => text.includes(normalize(fact)));
  const cited = [...new Set(citedIds(answer))].map((id) => sources.find((s) => s.id === id)).filter((s) => s !== undefined);
  const citation = cited.length ? cited.filter((s) => relevant(s, q)).length / cited.length : null;
  let isolated: boolean | null = null;
  if (q.type === "isolation") {
    const onlyExpected = sources.every((s) => inSection(s.sectionPath, q.expected_section) || inSection(s.sectionPath, "Statistical Annex"));
    isolated = onlyExpected && !q.must_not_contain.some((fact) => text.includes(normalize(fact)));
  }
  const pass = hit && facts && citation !== null && citation > 0 && isolated !== false;
  return { hit, facts, citation, verifier, refused: null, isolated, pass };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd web && npm test`
Expected: 105 passed, 6 skipped

- [ ] **Step 5: Commit**

```bash
git add web/lib/eval/score.ts web/lib/eval/score.test.ts
git commit -m "Add evaluation scoring"
```

---

### Task 4: Judge and runner

**Files:**
- Create: `web/lib/eval/judge.ts`, `web/lib/eval/run.ts`
- Test: `web/lib/eval/run.test.ts`

**Interfaces:**
- Consumes: Tasks 1–3; `answer`, `Deps` and `ChatEvent` (sub-project 6); `generateJson` and `GenClient` (sub-project 6); `errorMessage` (sub-project 1).
- Produces:
  - `Judgement = { covered; total; unsupported: string[] }`, `judgePrompt(q, answer, sources)`, `judge(client, model, q, answer, sources)`
  - `EvalConfig = { name; options; model: "chat" | "utility" }`
  - `MAIN`, and `ABLATIONS` (`full (lite)`, `vectorOnly`, `noRerank`, `noRouter`, `noFootnotes`)
  - `Result`, `runQuestion(q, config, deps): Promise<Outcome>`, `runSet(questions, configs, depsFor, { judge?, onResult? }): Promise<Result[]>`, `shareEmbeddings(deps, cache)`

- [ ] **Step 1: Write the failing test.** Create `web/lib/eval/run.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { judge, judgePrompt } from "@/lib/eval/judge";
import type { EvalQuestion } from "@/lib/eval/questions";
import { ABLATIONS, MAIN, runQuestion, runSet, shareEmbeddings } from "@/lib/eval/run";
import type { Deps } from "@/lib/rag/answer";
import type { GenClient } from "@/lib/rag/genai";
import { candidate } from "@/lib/rag/testing";

const SAR = "Ch. 2 Regional Outlooks › South Asia";
const q = (over: Partial<EvalQuestion> = {}): EvalQuestion => ({
  id: "gep-south-asia", type: "brief", scope: "gep", question: "What is the growth outlook for South Asia?",
  must_contain: ["6.3%"], must_not_contain: [], expected_pages: [96], expected_section: SAR, ...over,
});

function deps(over: Partial<Deps> = {}): Deps {
  return {
    catalog: async () => ({ gep: { title: "GEP", sections: [{ path: SAR, level: 2, kind: "region" }] } }),
    condense: async (_h, question) => question,
    embed: async () => [0.1],
    search: async () => [candidate(1, { content: "Growth in SAR is expected to soften to 6.3 percent in 2026." })],
    rerank: async () => new Map([[1, 3]]),
    neighbours: async () => new Map(),
    async *generate() {
      yield "Growth softens to 6.3 percent [S1].";
    },
    ...over,
  };
}

describe("runQuestion", () => {
  it("collects the answer, sources and warnings the UI would show", async () => {
    const outcome = await runQuestion(q(), MAIN, deps());
    expect(outcome).toMatchObject({ answer: "Growth softens to 6.3 percent [S1].", warnings: [] });
    expect(outcome.sources.map((s) => s.sectionPath)).toEqual([SAR]);
  });

  it("records a failed generation or a crash as an error", async () => {
    const failing = deps({
      async *generate() {
        throw new Error("503");
      },
    });
    expect((await runQuestion(q(), MAIN, failing)).error).toBe("answer generation failed: 503");
    expect((await runQuestion(q(), MAIN, deps({ catalog: async () => Promise.reject(new Error("db down")) }))).error).toBe("db down");
  });
});

describe("runSet", () => {
  it("runs every config over every question, passes options through and judges rubric questions only", async () => {
    const seen: { config: string; keyword?: boolean }[] = [];
    const judged: string[] = [];
    const results = await runSet(
      [q(), q({ id: "gep-chapter-3", rubric: ["point"] })],
      [MAIN, ABLATIONS[1]],
      (config) => deps({ search: async (args) => (seen.push({ config: config.name, keyword: args.keyword }), [candidate(1, { content: "6.3 percent" })]) }),
      { judge: async (question) => (judged.push(question.id), { covered: 1, total: 1, unsupported: [] }) },
    );
    expect(results.map((r) => `${r.config}:${r.id}:${r.score.pass}`)).toEqual([
      "full:gep-south-asia:true", "full:gep-chapter-3:true", "vectorOnly:gep-south-asia:true", "vectorOnly:gep-chapter-3:true",
    ]);
    expect(seen.map((s) => s.keyword)).toEqual([true, true, false, false]);
    expect(judged).toEqual(["gep-chapter-3", "gep-chapter-3"]);
    expect(results[1].judge).toEqual({ covered: 1, total: 1, unsupported: [] });
  });

  it("embeds each question once across configs", async () => {
    let calls = 0;
    const cache = new Map<string, Promise<number[]>>();
    const shared = () => shareEmbeddings(deps({ embed: async () => (calls++, [0.1]) }), cache);
    await runSet([q()], ABLATIONS, shared);
    expect(calls).toBe(1);
  });
});

describe("judge", () => {
  it("lists the rubric and sources, and counts only real rubric points", async () => {
    const question = q({ rubric: ["Debt raises interest rates", "Non-linear"] });
    const prompt = judgePrompt(question, "Debt raises rates [S1].", [{ id: 1, slug: "gep", doc: "GEP", sectionPath: "Ch. 3", pageStart: 109, pageEnd: 109, kind: "text", snippet: "", text: "Debt raises rates." }]);
    expect(prompt).toContain("1. Debt raises interest rates\n2. Non-linear");
    expect(prompt).toContain("[S1] Debt raises rates.");
    const client: GenClient = {
      models: {
        generateContent: async () => ({ text: '{"covered":[1,1,7],"unsupported":["rates doubled"]}' }),
        generateContentStream: async () => (async function* () {})(),
      },
    };
    expect(await judge(client, "lite", question, "a", [])).toEqual({ covered: 1, total: 2, unsupported: ["rates doubled"] });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- lib/eval/run.test.ts`
Expected: FAIL with `Cannot find package '@/lib/eval/judge'`

- [ ] **Step 3: Write `web/lib/eval/judge.ts`**

```ts
import type { EvalQuestion } from "@/lib/eval/questions";
import type { SourceInfo } from "@/lib/rag/answer";
import { generateJson, type GenClient } from "@/lib/rag/genai";

// Gemini judge for the broad GEP questions (spec §8.2): rubric coverage and claims the sources don't support.

export type Judgement = { covered: number; total: number; unsupported: string[] };

const SCHEMA = {
  type: "object",
  properties: {
    covered: { type: "array", items: { type: "integer" } },
    unsupported: { type: "array", items: { type: "string" } },
  },
  required: ["covered", "unsupported"],
};

export function judgePrompt(q: EvalQuestion, answer: string, sources: SourceInfo[]): string {
  const rubric = (q.rubric ?? []).map((point, i) => `${i + 1}. ${point}`).join("\n");
  const context = sources.map((s) => `[S${s.id}] ${s.text}`).join("\n\n");
  return (
    "You grade an answer to a question about a report.\n" +
    "covered: the numbers of the rubric points the answer states (in any wording).\n" +
    "unsupported: claims in the answer that the sources do not support (empty if none). Quote each claim briefly.\n\n" +
    `Question: ${q.question}\n\nRubric:\n${rubric}\n\nAnswer:\n${answer}\n\nSources:\n${context}`
  );
}

export async function judge(client: GenClient, model: string, q: EvalQuestion, answer: string, sources: SourceInfo[]): Promise<Judgement> {
  const total = q.rubric?.length ?? 0;
  const result = await generateJson<{ covered: number[]; unsupported: string[] }>(client, model, judgePrompt(q, answer, sources), SCHEMA);
  const covered = new Set(result.covered.filter((n) => Number.isInteger(n) && n >= 1 && n <= total)).size;
  return { covered, total, unsupported: result.unsupported };
}
```

- [ ] **Step 4: Write `web/lib/eval/run.ts`**

```ts
import type { Judgement } from "@/lib/eval/judge";
import type { EvalQuestion } from "@/lib/eval/questions";
import { scoreAnswer, type Outcome, type Score } from "@/lib/eval/score";
import { answer, type ChatEvent, type Deps, type Options } from "@/lib/rag/answer";
import { errorMessage } from "@/lib/rag/health";

/** A pipeline setting to evaluate: ablation switches and which model writes the answers. */
export type EvalConfig = { name: string; options: Options; model: "chat" | "utility" };

export const MAIN: EvalConfig = { name: "full", options: {}, model: "chat" };
// Ablations answer with the utility model (Flash allows ~20 answers a day), against a utility-model baseline.
export const ABLATIONS: EvalConfig[] = [
  { name: "full (lite)", options: {}, model: "utility" },
  { name: "vectorOnly", options: { vectorOnly: true }, model: "utility" },
  { name: "noRerank", options: { noRerank: true }, model: "utility" },
  { name: "noRouter", options: { noRouter: true }, model: "utility" },
  { name: "noFootnotes", options: { noFootnotes: true }, model: "utility" },
];

export type Result = {
  id: string; type: EvalQuestion["type"]; scope: EvalQuestion["scope"]; config: string; ms: number;
  answer: string; sources: { id: number; sectionPath: string; pageStart: number; pageEnd: number; kind: string }[];
  warnings: string[]; error?: string; score: Score; judge?: Judgement;
};

/** Ask one question through the real orchestrator and collect what the UI would show. */
export async function runQuestion(q: EvalQuestion, config: EvalConfig, deps: Deps): Promise<Outcome> {
  const outcome: Outcome = { answer: "", sources: [], warnings: [] };
  const emit = (event: ChatEvent) => {
    if (event.type === "sources") outcome.sources = event.sources;
    else if (event.type === "text") outcome.answer += event.delta;
    else if (event.type === "done") outcome.warnings = event.warnings;
    else outcome.error = event.message;
  };
  try {
    await answer({ question: q.question, scope: q.scope, history: [], options: config.options }, deps, emit);
  } catch (error) {
    outcome.error = errorMessage(error);
  }
  return outcome;
}

type RunOptions = {
  judge?: (q: EvalQuestion, outcome: Outcome) => Promise<Judgement>; // rubric questions only
  onResult?: (result: Result) => void;
};

/** Every question under every config, one at a time (free-tier rate limits). */
export async function runSet(questions: EvalQuestion[], configs: EvalConfig[], depsFor: (config: EvalConfig) => Deps, options: RunOptions = {}): Promise<Result[]> {
  const results: Result[] = [];
  for (const config of configs) {
    const deps = depsFor(config);
    for (const q of questions) {
      const started = Date.now();
      const outcome = await runQuestion(q, config, deps);
      const result: Result = {
        id: q.id, type: q.type, scope: q.scope, config: config.name, ms: Date.now() - started,
        answer: outcome.answer, warnings: outcome.warnings, error: outcome.error, score: scoreAnswer(q, outcome),
        sources: outcome.sources.map(({ id, sectionPath, pageStart, pageEnd, kind }) => ({ id, sectionPath, pageStart, pageEnd, kind })),
      };
      if (options.judge && q.rubric && !outcome.error) {
        result.judge = await options.judge(q, outcome).catch(() => undefined);
      }
      results.push(result);
      options.onResult?.(result);
    }
  }
  return results;
}

/** Share query embeddings across configs: the same question is embedded once per run. */
export function shareEmbeddings(deps: Deps, cache: Map<string, Promise<number[]>>): Deps {
  return {
    ...deps,
    embed: (question) => {
      if (!cache.has(question)) cache.set(question, deps.embed(question).catch((error) => {
        cache.delete(question); // don't keep a failed call
        throw error;
      }));
      return cache.get(question)!;
    },
  };
}
```

- [ ] **Step 5: Run the tests**

Run: `cd web && npm test`
Expected: 110 passed, 6 skipped

- [ ] **Step 6: Commit**

```bash
git add web/lib/eval/judge.ts web/lib/eval/run.ts web/lib/eval/run.test.ts
git commit -m "Add evaluation judge and runner"
```

---

### Task 5: Reports and `npm run eval`

**Files:**
- Create: `web/lib/eval/report.ts`, `web/lib/eval/cli.ts`, `web/scripts/eval.ts`
- Modify: `web/package.json` and `web/package-lock.json` (`tsx` and the `eval` script), `.gitignore`, `README.md`
- Test: `web/lib/eval/report.test.ts`

**Interfaces:**
- Consumes: Tasks 1–4; `getEnv`, `utilityModel`, `getPool`, `getGenAI` (sub-projects 1 and 6).
- Produces:
  - `scorecard(results)`, `passBar(results): { lines; passed }`, `ablationTable(results)`, `progressLine(result)`
  - `Args`, `parseArgs(argv)`, `main(argv, root): Promise<number>`
  - `npm run eval [-- --ablate [--config <name>]] [--only <id,id>]`

- [ ] **Step 1: Write the failing test.** Create `web/lib/eval/report.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseArgs } from "@/lib/eval/cli";
import { ablationTable, passBar, scorecard } from "@/lib/eval/report";
import type { Result } from "@/lib/eval/run";
import type { Score } from "@/lib/eval/score";

const score = (over: Partial<Score>): Score => ({ hit: true, facts: true, citation: 1, verifier: true, refused: null, isolated: null, pass: true, ...over });
const result = (id: string, type: Result["type"], scope: Result["scope"], over: Partial<Score> = {}, config = "full"): Result => ({
  id, type, scope, config, ms: 1000, answer: "", sources: [], warnings: [], score: score(over),
});

describe("scorecard", () => {
  it("shows one row per question with each metric", () => {
    const text = scorecard([
      { ...result("gep-chapter-3", "brief", "gep", { citation: 0.75 }), judge: { covered: 5, total: 7, unsupported: ["x"] } },
      result("trap-world-cup", "trap", "all", { hit: null, facts: null, citation: null, verifier: null, refused: true }),
    ]);
    expect(text.split("\n")[0]).toMatch(/^question\s+type\s+hit\s+facts\s+cited\s+verifier\s+refused\s+isolated\s+rubric\s+result$/);
    expect(text).toMatch(/gep-chapter-3\s+brief\s+✓\s+✓\s+75%\s+✓\s+–\s+–\s+5\/7 \(1 unsupported\)\s+PASS/);
    expect(text).toMatch(/trap-world-cup\s+trap\s+–\s+–\s+–\s+–\s+✓\s+–\s+–\s+PASS/);
  });
});

describe("passBar", () => {
  it("checks the four pass-bar criteria", () => {
    const results = [
      result("b1", "brief", "gpo"), result("b2", "brief", "gep", { pass: false, facts: false }),
      result("t1", "trap", "imac", { refused: true }), result("i1", "isolation", "gep", { isolated: true }),
      result("c1", "coverage", "imac", { verifier: false }),
    ];
    expect(passBar(results)).toEqual({
      lines: ["✗ Brief questions answered with citations: 1/2", "✓ Trap questions refused: 1/1", "✓ Region isolation: 1/1", "✗ Answers with every figure verified: 3/4"],
      passed: false,
    });
  });
});

describe("ablationTable", () => {
  it("counts passes per document shape for each config", () => {
    const text = ablationTable([
      result("g", "brief", "gpo"), result("t", "trap", "all"),
      result("g", "brief", "gpo", { pass: false }, "noRerank"), result("t", "trap", "all", { pass: false }, "noRerank"),
    ]);
    expect(text.split("\n")).toEqual([
      "config    GPO  GEP  iMac  traps  all",
      "full      1/1  –    –     1/1    2/2",
      "noRerank  0/1  –    –     0/1    0/2",
    ]);
  });
});

describe("parseArgs", () => {
  it("reads --ablate, --config and --only, and rejects anything else", () => {
    expect(parseArgs([])).toEqual({ ablate: false });
    expect(parseArgs(["--ablate", "--config", "noRerank", "--only", "trap-world-cup, gep-south-asia"])).toEqual({
      ablate: true, config: "noRerank", only: ["trap-world-cup", "gep-south-asia"],
    });
    expect(() => parseArgs(["--config", "noRerank"])).toThrow("--config needs --ablate");
    expect(() => parseArgs(["--ablate", "--config", "fast"])).toThrow('unknown config "fast"');
    expect(() => parseArgs(["--verbose"])).toThrow("unknown or incomplete option: --verbose");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- lib/eval/report.test.ts`
Expected: FAIL with `Cannot find package '@/lib/eval/cli'`

- [ ] **Step 3: Write `web/lib/eval/report.ts`**

```ts
import type { Result } from "@/lib/eval/run";

// Plain-text tables for the terminal (spec §8.2, §8.3).

const mark = (value: boolean | null) => (value === null ? "–" : value ? "✓" : "✗");

function table(rows: string[][]): string {
  const widths = rows[0].map((_, i) => Math.max(...rows.map((row) => row[i].length)));
  return rows.map((row) => row.map((cell, i) => cell.padEnd(widths[i])).join("  ").trimEnd()).join("\n");
}

export function scorecard(results: Result[]): string {
  const rows = results.map((r) => {
    const { hit, facts, citation, verifier, refused, isolated, pass } = r.score;
    const judged = r.judge ? `${r.judge.covered}/${r.judge.total}${r.judge.unsupported.length ? ` (${r.judge.unsupported.length} unsupported)` : ""}` : "–";
    return [r.id, r.type, mark(hit), mark(facts), citation === null ? "–" : `${Math.round(citation * 100)}%`, mark(verifier),
      mark(refused), mark(isolated), judged, pass ? "PASS" : r.error ? "ERROR" : "fail"];
  });
  return table([["question", "type", "hit", "facts", "cited", "verifier", "refused", "isolated", "rubric", "result"], ...rows]);
}

/** The pass bar of spec §1, from one config's results. */
export function passBar(results: Result[]): { lines: string[]; passed: boolean } {
  const of = (type: string) => results.filter((r) => r.type === type);
  const passed = (rs: Result[]) => rs.filter((r) => r.score.pass).length;
  const answered = results.filter((r) => r.type !== "trap" && !r.error);
  const clean = answered.filter((r) => r.score.verifier).length;
  const checks: [string, number, number][] = [
    ["Brief questions answered with citations", passed(of("brief")), of("brief").length],
    ["Trap questions refused", passed(of("trap")), of("trap").length],
    ["Region isolation", passed(of("isolation")), of("isolation").length],
    ["Answers with every figure verified", clean, answered.length],
  ];
  return {
    lines: checks.map(([label, got, total]) => `${got === total && total > 0 ? "✓" : "✗"} ${label}: ${got}/${total}`),
    passed: checks.every(([, got, total]) => got === total && total > 0),
  };
}

const GROUPS: [string, (r: Result) => boolean][] = [
  ["GPO", (r) => r.type !== "trap" && r.scope === "gpo"],
  ["GEP", (r) => r.type !== "trap" && r.scope === "gep"],
  ["iMac", (r) => r.type !== "trap" && r.scope === "imac"],
  ["traps", (r) => r.type === "trap"],
  ["all", () => true],
];

/** Questions passed per document shape, one row per config, so each technique's contribution shows. */
export function ablationTable(results: Result[]): string {
  const configs = [...new Set(results.map((r) => r.config))];
  const rows = configs.map((config) => [
    config,
    ...GROUPS.map(([, inGroup]) => {
      const rs = results.filter((r) => r.config === config && inGroup(r));
      return rs.length ? `${rs.filter((r) => r.score.pass).length}/${rs.length}` : "–";
    }),
  ]);
  return table([["config", ...GROUPS.map(([name]) => name)], ...rows]);
}

export function progressLine(r: Result): string {
  return `${r.config.padEnd(12)} ${r.id.padEnd(22)} ${r.score.pass ? "PASS" : r.error ? `ERROR ${r.error.slice(0, 80)}` : "fail"} (${(r.ms / 1000).toFixed(1)} s)`;
}
```

- [ ] **Step 4: Write `web/lib/eval/cli.ts`**

```ts
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { getEnv, utilityModel } from "@/lib/env";
import { judge } from "@/lib/eval/judge";
import { loadQuestions } from "@/lib/eval/questions";
import { ablationTable, passBar, progressLine, scorecard } from "@/lib/eval/report";
import { ABLATIONS, MAIN, runSet, shareEmbeddings, type EvalConfig } from "@/lib/eval/run";
import { getPool } from "@/lib/rag/db";
import { productionDeps } from "@/lib/rag/deps";
import { getGenAI } from "@/lib/rag/gemini";
import type { GenClient } from "@/lib/rag/genai";

export type Args = { ablate: boolean; config?: string; only?: string[] };

const USAGE = "usage: npm run eval [-- --ablate [--config <name>]] [--only <id,id>]";

export function parseArgs(argv: string[]): Args {
  const args: Args = { ablate: false };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "--ablate") args.ablate = true;
    else if (flag === "--config" && argv[i + 1]) args.config = argv[++i];
    else if (flag === "--only" && argv[i + 1]) args.only = argv[++i].split(",").map((id) => id.trim()).filter(Boolean);
    else throw new Error(`${USAGE}\nunknown or incomplete option: ${flag}`);
  }
  if (args.config && !args.ablate) throw new Error(`--config needs --ablate\n${USAGE}`);
  if (args.config && !ABLATIONS.some((c) => c.name === args.config)) {
    throw new Error(`unknown config "${args.config}"; choose one of: ${ABLATIONS.map((c) => c.name).join(", ")}`);
  }
  return args;
}

/** Run the evaluation against the live pipeline and write eval/results/<timestamp>.json. Returns the exit code. */
export async function main(argv: string[], root: string): Promise<number> {
  const args = parseArgs(argv);
  const all = loadQuestions(path.join(root, "eval", "questions.yaml"));
  const unknown = (args.only ?? []).filter((id) => !all.some((q) => q.id === id));
  if (unknown.length) throw new Error(`unknown question ids: ${unknown.join(", ")}`);
  const questions = args.only ? all.filter((q) => args.only!.includes(q.id)) : all;
  const configs: EvalConfig[] = args.ablate ? ABLATIONS.filter((c) => !args.config || c.name === args.config) : [MAIN];

  const env = getEnv();
  const helper = utilityModel(env);
  const client = getGenAI() as unknown as GenClient;
  const embeddings = new Map<string, Promise<number[]>>();
  const depsFor = (config: EvalConfig) =>
    shareEmbeddings(productionDeps(undefined, { chatModel: config.model === "utility" ? helper : env.GEMINI_CHAT_MODEL }), embeddings);

  const startedAt = new Date();
  console.log(`${questions.length} questions × ${configs.length} config(s); answers: ${configs.map((c) => `${c.name}=${c.model === "utility" ? helper : env.GEMINI_CHAT_MODEL}`).join(", ")}`);
  try {
    const results = await runSet(questions, configs, depsFor, {
      judge: args.ablate ? undefined : (q, outcome) => judge(client, helper, q, outcome.answer, outcome.sources),
      onResult: (result) => console.log(progressLine(result)),
    });
    const dir = path.join(root, "eval", "results");
    mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${startedAt.toISOString().replace(/[:.]/g, "-")}${args.ablate ? "-ablate" : ""}.json`);
    const models = { chat: env.GEMINI_CHAT_MODEL, utility: helper, embed: env.GEMINI_EMBED_MODEL };
    writeFileSync(file, JSON.stringify({ startedAt, models, configs, results }, null, 2));
    console.log(`\n${args.ablate ? ablationTable(results) : scorecard(results)}\n`);
    console.log(`results: ${path.relative(root, file)}`);
    if (args.ablate) return 0;
    if (args.only) {
      console.log("\npartial run (--only): the pass bar needs the full question set");
      return 0;
    }
    const bar = passBar(results);
    console.log(`\n${bar.lines.join("\n")}\n${bar.passed ? "PASS BAR MET" : "PASS BAR NOT MET"}`);
    return bar.passed ? 0 : 1;
  } finally {
    await getPool().end();
  }
}
```

- [ ] **Step 5: Install tsx and add the npm script**

Run: `cd web && npm install -D tsx@4.20.6 && npm pkg set scripts.eval="tsx scripts/eval.ts"`
Expected: `package.json` lists `"tsx": "^4.20.6"` and `"eval": "tsx scripts/eval.ts"`

- [ ] **Step 6: Write `web/scripts/eval.ts`**

```ts
// Evaluation CLI (spec §8). From web/: npm run eval [-- --ablate [--config <name>]] [--only <id,id>]
import path from "node:path";
import { loadEnvConfig } from "@next/env";
import { main } from "@/lib/eval/cli";

const root = path.resolve(__dirname, "..", ".."); // repo root: eval/questions.yaml, eval/results/, .env
loadEnvConfig(root);
main(process.argv.slice(2), root).then(
  (code) => (process.exitCode = code),
  (error: unknown) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 2;
  },
);
```

- [ ] **Step 7: Ignore run outputs.** Append to `.gitignore`:

```gitignore

# evaluation runs (npm run eval)
eval/results/
```

- [ ] **Step 8: Run the tests, type check, lint and the argument checks**

Run: `cd web && npm test && npm run test:int && npx tsc --noEmit && npx eslint .`
Expected: 114 passed, 6 skipped; then 119 passed, 1 skipped; no type errors; eslint shows only the existing `_omit` warning

Run: `cd web && npm run eval -- --verbose; echo "exit $?"; npm run eval -- --only nope; echo "exit $?"`
Expected: the usage line plus `unknown or incomplete option: --verbose`, then `unknown question ids: nope`, each with `exit 2`. No Gemini calls are made.

- [ ] **Step 9: Smoke-test one question live.** This costs 1 embedding and 1 Lite rerank; the trap is refused before generation.

Run: `cd web && npm run eval -- --only trap-world-cup`
Expected: `full  trap-world-cup  PASS`, a one-row scorecard, `results: eval/results/<timestamp>.json` and `partial run (--only): …`

- [ ] **Step 10: Document the evaluation.** Append to `README.md`:

````markdown
## Evaluation

```bash
cd web
npm run eval                            # 18 questions on the full pipeline (Flash answers), pass bar of spec §1
npm run eval -- --only gep-south-asia   # a subset (no pass bar)
npm run eval -- --ablate                # vectorOnly / noRerank / noRouter / noFootnotes vs full, on Flash-Lite
npm run eval -- --ablate --config noRerank
```

Questions live in `eval/questions.yaml`; every expected fact was copied from the PDF page it cites. Each run prints a
scorecard (retrieval hit, fact match, citation precision, verifier, trap refusal, isolation, rubric judge) and writes
`eval/results/<timestamp>.json` (git-ignored). A main run costs ~14 Flash answers of the free tier's ~20 a day.
````

- [ ] **Step 11: Commit**

```bash
git add web/lib/eval/report.ts web/lib/eval/report.test.ts web/lib/eval/cli.ts web/scripts/eval.ts web/package.json web/package-lock.json .gitignore README.md
git commit -m "Add evaluation command"
```

---

### Task 6: Run the evaluation

**Files:**
- Modify: `README.md` (results)

This task spends real quota. Check first that the chat model responds. If Flash returns 503 "high demand" or the daily quota is spent, stop and run the task later; don't change models to get a pass.

- [ ] **Step 1: Main run.** It uses about 14 Flash answers and 2 Lite judge calls.

Run: `cd web && npm run eval`
Expected: 18 progress lines, the scorecard and the four pass-bar lines. If every line is ✓, it ends with `PASS BAR MET` and exits 0.

- [ ] **Step 2: Ablation run.** It uses about 70 Lite answers and 72 Lite reranks; it can be split across days with `--config <name>`.

Run: `cd web && npm run eval -- --ablate`
Expected: 90 progress lines and a table with rows `full (lite)`, `vectorOnly`, `noRerank`, `noRouter`, `noFootnotes`, and columns `GPO`, `GEP`, `iMac`, `traps` and `all`.

- [ ] **Step 3: Record the results.** Append to `README.md` a `### Results (<date>)` section under "Evaluation":
- the models used;
- the scorecard and pass-bar lines from Step 1;
- the ablation table from Step 2;
- one sentence per ablation row saying what the change cost.

Use the numbers exactly as printed. Every question that failed, and every pass-bar line marked ✗, is a finding for the final review, not something to patch in this task.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Record evaluation results"
```

---

## Sub-project acceptance (spec §10, row 8)

- [ ] `cd web && npm test` → 114 passed, 6 skipped; `npm run test:int` → 119 passed, 1 skipped; `tsc` and eslint clean
- [ ] `npm run eval` prints `PASS BAR MET` (spec §1). If not, the failures are reported and the final review decides what to fix.
- [ ] `npm run eval -- --ablate` produced the ablation table, recorded in `README.md`
- [ ] `git log --oneline` shows the spec update, the plan and 6 short task commits, with no trailers

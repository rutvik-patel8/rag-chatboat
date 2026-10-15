# Retrieval API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `POST /api/chat`. It turns a question into a grounded, cited answer over the three ingested documents and streams it as NDJSON events (sources, then text deltas, then warnings and debug), refusing when nothing relevant is found.

**Architecture:** A pure orchestrator, `lib/rag/answer.ts`, runs the spec §6 flow:
1. condense follow-ups
2. route deterministically (documents and section filters)
3. embed the question
4. run hybrid search (pgvector + Postgres full-text, fused with RRF in one SQL statement)
5. rerank with the utility model
6. refuse below the relevance bar
7. assemble the context (neighbouring chunks, footnotes, `[S#]` labels)
8. stream the chat model's answer
9. verify citations and figures

Every external call goes through an injected `Deps` object, so the flow is unit-tested with fakes. `lib/rag/deps.ts` wires the real Postgres pool and `@google/genai`. The route validates the request, checks the embedding/schema contract once, and streams the events.

**Tech Stack:** Next.js 16.4 route handler, TypeScript, `@google/genai` (`generateContent` with JSON schema, `generateContentStream`), `pg`, `zod`, vitest. The database is the one ingested by sub-projects 2–5.

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`. This plan implements §10 row 6 using §6 (updated in commit `aa5b85f`: deterministic routing, NDJSON, utility model) and §7.

## Global Constraints

- Run every npm command from `web/`. Docker DB must be up. Integration tests run with `npm run test:int`.
- Answers use `GEMINI_CHAT_MODEL`. Condensing and reranking use `utilityModel(env)`: `GEMINI_UTILITY_MODEL`, then `GEMINI_SUMMARY_MODEL`, then `GEMINI_CHAT_MODEL`.
- Hybrid search: vector top **40** ∪ keyword top **40**, RRF **k = 60**, **20** candidates to the reranker. Rerank keeps relevance **≥ 2** (of 0–3), at most **8**. The context budget is **24,000** characters.
- Routing is deterministic (no LLM call). A section match is a hard filter on that section and its descendants (`path = p` or `starts_with(path, p || ' › ')`).
- The refusal text is exactly `I couldn't find this in <document title | the documents>.`, optionally followed by ` Closest sections: a; b; c.`. No generation call is made for a refusal.
- Request: `question` 1–1000 chars (trimmed), `scope` ∈ `gpo|gep|imac|all` (default `all`), `history` ≤ 6 turns. 400 on invalid; 503 on schema mismatch or unreachable DB.
- Stream: `Content-Type: application/x-ndjson; charset=utf-8`, one JSON event per line: `sources` → `text`* → `done`, or `error`.
- Commits: one per task, short imperative subject only (≤ 50 chars). **No `Co-Authored-By` trailer, no body.**

## Review Focus

1. **Region and chapter questions must not mix sources.** "South Asia" must never retrieve East Asia passages, and "Chapter 3" stays in Chapter 3. Pinned by the router tests (Task 2) and the live-DB search test (Task 4).
2. **Hallucinated or altered figures.** Any number in the answer that isn't in a cited source, a citation to a source that doesn't exist, or a missing citation becomes a warning. Digits inside words (`CO2e`, `M4`) and the `[S#]` labels themselves must not count. Pinned in Task 1.
3. **Nothing relevant (trap questions).** The reply is a refusal, and the answer model is never called. Pinned in Task 5.
4. **Helper or model failures** (free-tier 429s are common). A failed rerank falls back to search order with a warning. A failed condense uses the original question. A failed generation sends an `error` event. None of these crash the stream. Pinned in Tasks 3, 5 and 6.
5. **Bad input.** Empty, oversized or wrong-shaped bodies get HTTP 400 with a readable reason, and history is capped. Pinned in Task 6.

---

### Task 1: Answer verification helpers

**Files:**
- Create: `web/lib/rag/numbers.ts`, `web/lib/rag/verify.ts`
- Test: `web/lib/rag/verify.test.ts`

**Interfaces:**
- Produces: `numbersIn(text: string): string[]` (figures compared by value; `[S#]` and digits inside words ignored); `type SourceText = { id: number; label: string; text: string }`; `verifyAnswer(answer: string, sources: SourceText[]): string[]` (warnings).

- [ ] **Step 1: Write the failing tests.** Create `web/lib/rag/verify.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { numbersIn } from "@/lib/rag/numbers";
import { verifyAnswer } from "@/lib/rag/verify";

describe("numbersIn", () => {
  it("compares figures by value and ignores citations and digits inside words", () => {
    expect(numbersIn("GPO staff visited 166 libraries [S1] and distributed 802,834 copies in FY 2018")).toEqual(["166", "802834", "2018"]);
    expect(numbersIn("growth of 6.30 percent, a −0.1 revision, CO2e and M4 chips")).toEqual(["6.3", "-0.1"]);
  });
});

describe("verifyAnswer", () => {
  const sources = [
    { id: 1, label: "[S1] GEP › Ch. 2 Regional Outlooks › South Asia | p.96 | text", text: "Growth in SAR is expected to soften to 6.3 percent in 2026." },
    { id: 2, label: "[S2] GEP › Ch. 2 Regional Outlooks › East Asia and Pacific | p.81 | text", text: "Growth is forecast at 4.4 percent." },
  ];

  it("accepts figures that appear in the cited sources", () => {
    expect(verifyAnswer("Growth in South Asia is expected to soften to 6.3 percent in 2026 [S1].", sources)).toEqual([]);
  });

  it("flags figures that only appear in sources the sentence does not cite, and unknown citations", () => {
    expect(verifyAnswer("South Asia will grow 4.4 percent [S1] [S7].", sources)).toEqual([
      "cites unknown sources: S7",
      "unverified figures: 4.4",
    ]);
  });

  it("flags an answer without citations", () => {
    expect(verifyAnswer("Growth slows.", sources)).toEqual(["no citations"]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/rag/verify.test.ts`
Expected: FAIL with `Cannot find package '@/lib/rag/numbers'`

- [ ] **Step 3: Write `web/lib/rag/numbers.ts`**

```ts
// Figures in an answer must appear in the sources it cites (spec §6.8). Numbers are compared by value:
// "802,834" = "802834", "6.30" = "6.3", "−0.1" = "-0.1"; "58 percent" and "58%" both yield 58.
const NUMBER = /(?<![\w.])[-−–]?\d[\d,]*(?:\.\d+)?/g;
const CITATION = /\[S\d+\]/g;

export function numbersIn(text: string): string[] {
  const matches = text.replace(CITATION, " ").match(NUMBER) ?? [];
  return matches.map((raw) => String(Number(raw.replace(/[−–]/, "-").replace(/,/g, ""))));
}
```

- [ ] **Step 4: Write `web/lib/rag/verify.ts`**

```ts
import { numbersIn } from "@/lib/rag/numbers";

export type SourceText = { id: number; label: string; text: string };

/** Deterministic checks after generation: citations exist and every figure appears in a cited source. */
export function verifyAnswer(answer: string, sources: SourceText[]): string[] {
  const warnings: string[] = [];
  const cited = [...answer.matchAll(/\[S(\d+)\]/g)].map((match) => Number(match[1]));
  const known = new Set(sources.map((source) => source.id));
  const unknown = [...new Set(cited.filter((id) => !known.has(id)))];
  if (cited.length === 0) warnings.push("no citations");
  if (unknown.length) warnings.push(`cites unknown sources: ${unknown.map((id) => `S${id}`).join(", ")}`);
  const supported = new Set(
    sources.filter((source) => cited.includes(source.id)).flatMap((source) => numbersIn(`${source.label} ${source.text}`)),
  );
  const unsupported = [...new Set(numbersIn(answer).filter((value) => !supported.has(value)))];
  if (unsupported.length) warnings.push(`unverified figures: ${unsupported.join(", ")}`);
  return warnings;
}
```

- [ ] **Step 5: Run the tests**

Run: `cd web && npm test`
Expected: 32 passed, 2 skipped

- [ ] **Step 6: Commit**

```bash
git add web/lib/rag/numbers.ts web/lib/rag/verify.ts web/lib/rag/verify.test.ts
git commit -m "Add answer verification helpers"
```

---

### Task 2: Deterministic query router

**Files:**
- Create: `web/lib/rag/router.ts`
- Test: `web/lib/rag/router.test.ts`

**Interfaces:**
- Produces: `type Scope = "gpo" | "gep" | "imac" | "all"`; `type Catalog = Record<string, { title: string; sections: { path: string; level: number; kind: string }[] }>`; `type Route = { docs: string[]; paths: string[] }`; `REGIONS`; `routeQuestion(question: string, scope: Scope, catalog: Catalog): Route`.

- [ ] **Step 1: Write the failing tests.** Create `web/lib/rag/router.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { routeQuestion, type Catalog } from "@/lib/rag/router";

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

  it("an explicit scope wins and never adds GEP filters to other documents", () => {
    expect(routeQuestion("South Asia growth", "gpo", catalog)).toEqual({ docs: ["gpo"], paths: [] });
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/rag/router.test.ts`
Expected: FAIL with `Cannot find package '@/lib/rag/router'`

- [ ] **Step 3: Write `web/lib/rag/router.ts`**

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
  return { docs, paths: [...new Set(paths)] };
}
```

- [ ] **Step 4: Run the tests**

Run: `cd web && npm test`
Expected: 36 passed, 2 skipped

- [ ] **Step 5: Commit**

```bash
git add web/lib/rag/router.ts web/lib/rag/router.test.ts
git commit -m "Add deterministic query router"
```

---

### Task 3: Utility model, generation helpers and reranking

**Files:**
- Modify: `web/lib/env.ts` (optional `GEMINI_SUMMARY_MODEL`, `GEMINI_UTILITY_MODEL`; `utilityModel`)
- Create: `web/lib/rag/genai.ts`, `web/lib/rag/rerank.ts`, `web/lib/rag/testing.ts` (test helper)
- Test: `web/lib/env.test.ts`, `web/lib/rag/genai.test.ts`, `web/lib/rag/rerank.test.ts`

**Interfaces:**
- Consumes: `withRetry` from `@/lib/rag/gemini` (sub-project 1).
- Produces:
  - `utilityModel(env: Env): string`
  - `type GenClient`, `generateJson<T>(client, model, prompt, schema): Promise<T>`, `generateText(client, model, prompt): Promise<string>`, `streamText(client, model, system, prompt): AsyncGenerator<string>` (temperature 0.1, `systemInstruction`)
  - `KEEP = 8`, `MIN_RELEVANCE = 2`, `rerankPrompt(question, candidates)`, `rerank(client, model, question, candidates): Promise<Map<number, number>>` (scores clamped 0–3), `selectRelevant(candidates, scores)`
  - test helper `candidate(id, overrides?)`

- [ ] **Step 1: Write the failing tests.** Append to `web/lib/env.test.ts`:

```ts
describe("utilityModel", () => {
  it("prefers GEMINI_UTILITY_MODEL, then the summary model, then the chat model", async () => {
    const { utilityModel } = await import("@/lib/env");
    expect(utilityModel(parseEnv(VALID))).toBe("gemini-3.8-flash");
    expect(utilityModel(parseEnv({ ...VALID, GEMINI_SUMMARY_MODEL: "lite" }))).toBe("lite");
    expect(utilityModel(parseEnv({ ...VALID, GEMINI_SUMMARY_MODEL: "lite", GEMINI_UTILITY_MODEL: "tiny" }))).toBe("tiny");
  });
});
```

Create `web/lib/rag/testing.ts` (a test helper, also used by Tasks 4–5):

```ts
import type { Candidate } from "@/lib/rag/retrieve";

/** Test helper: a search candidate with sensible defaults. */
export function candidate(id: number, overrides: Partial<Candidate> = {}): Candidate {
  return {
    id, docSlug: "gep", docTitle: "Global Economic Prospects", sectionId: 10, sectionPath: "Ch. 2 Regional Outlooks › South Asia",
    seq: id, kind: "text", content: `passage ${id}`, pageStart: 96, pageEnd: 96, footnotes: [], score: 1 / (60 + id),
    vecRank: id, kwRank: null, ...overrides,
  };
}
```

Create `web/lib/rag/genai.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { generateJson, streamText, type GenClient } from "@/lib/rag/genai";

describe("genai helpers", () => {
  it("parses JSON answers and rejects empty ones", async () => {
    const client = (text: string): GenClient => ({
      models: { generateContent: async () => ({ text }), generateContentStream: async () => (async function* () {})() },
    });
    expect(await generateJson(client('{"a":1}'), "m", "p", {})).toEqual({ a: 1 });
    await expect(generateJson(client(""), "m", "p", {})).rejects.toThrow("returned no text");
  });

  it("streams text deltas with the system prompt at low temperature", async () => {
    const seen: { config?: Record<string, unknown> }[] = [];
    const client: GenClient = {
      models: {
        generateContent: async () => ({ text: "" }),
        async generateContentStream(req) {
          seen.push(req);
          return (async function* () {
            yield { text: "Hello " };
            yield {};
            yield { text: "world" };
          })();
        },
      },
    };
    const parts: string[] = [];
    for await (const part of streamText(client, "flash", "rules", "question")) parts.push(part);
    expect(parts).toEqual(["Hello ", "world"]);
    expect(seen[0].config).toEqual({ systemInstruction: "rules", temperature: 0.1 });
  });
});
```

Create `web/lib/rag/rerank.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { GenClient } from "@/lib/rag/genai";
import { rerank, rerankPrompt, selectRelevant } from "@/lib/rag/rerank";
import { candidate } from "@/lib/rag/testing";

const fakeClient = (text: string, seen: unknown[] = []): GenClient => ({
  models: {
    async generateContent(req) {
      seen.push(req);
      return { text };
    },
    async generateContentStream() {
      throw new Error("not used");
    },
  },
});

describe("rerank", () => {
  it("asks for JSON scores and clamps them to 0–3", async () => {
    const seen: { model: string; config?: Record<string, unknown> }[] = [];
    const scores = await rerank(fakeClient('{"scores":[{"id":1,"score":5},{"id":2,"score":-1}]}', seen), "lite", "q", [candidate(1), candidate(2)]);
    expect([...scores]).toEqual([[1, 3], [2, 0]]);
    expect(seen[0].model).toBe("lite");
    expect(seen[0].config).toMatchObject({ temperature: 0, responseMimeType: "application/json" });
  });

  it("shows each passage's id, location and text", () => {
    const prompt = rerankPrompt("Growth in South Asia?", [candidate(7, { content: "Growth in SAR is expected to soften." })]);
    expect(prompt).toContain("[7] Global Economic Prospects › Ch. 2 Regional Outlooks › South Asia (p.96, text)\nGrowth in SAR is expected to soften.");
  });
});

describe("selectRelevant", () => {
  it("keeps passages scoring 2 or more, best first, at most 8", () => {
    const candidates = Array.from({ length: 12 }, (_, i) => candidate(i + 1));
    const scores = new Map(candidates.map((c) => [c.id, c.id === 5 ? 3 : c.id % 2 ? 2 : 1]));
    expect(selectRelevant(candidates, scores).map((c) => c.id)).toEqual([5, 1, 3, 7, 9, 11]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/env.test.ts lib/rag/genai.test.ts lib/rag/rerank.test.ts`
Expected: FAIL. `utilityModel` is not a function, and `@/lib/rag/genai` and `@/lib/rag/rerank` cannot be found. Note that `testing.ts` imports a type from `retrieve.ts`, which Task 4 creates; vitest strips types, so this works.

- [ ] **Step 3: Add the utility model.** In `web/lib/env.ts`, replace

```ts
  EMBED_DIM: z.coerce.number().int().min(128).max(3072),
});
```

with

```ts
  EMBED_DIM: z.coerce.number().int().min(128).max(3072),
  GEMINI_SUMMARY_MODEL: z.string().trim().optional(),
  GEMINI_UTILITY_MODEL: z.string().trim().optional(), // reranking and follow-up rewriting
});
```

and replace

```ts
let cached: Env | undefined;
```

with

```ts
/** Model for small helper calls: GEMINI_UTILITY_MODEL, else the summary model, else the chat model. */
export function utilityModel(env: Env): string {
  return env.GEMINI_UTILITY_MODEL || env.GEMINI_SUMMARY_MODEL || env.GEMINI_CHAT_MODEL;
}

let cached: Env | undefined;
```

- [ ] **Step 4: Write `web/lib/rag/genai.ts`**

```ts
import { withRetry } from "@/lib/rag/gemini";

/** The slice of @google/genai used for text generation; tests pass fakes with the same shape. */
export type GenClient = {
  models: {
    generateContent(req: { model: string; contents: string; config?: Record<string, unknown> }): Promise<{ text?: string }>;
    generateContentStream(req: { model: string; contents: string; config?: Record<string, unknown> }): Promise<AsyncIterable<{ text?: string }>>;
  };
};

export async function generateJson<T>(client: GenClient, model: string, prompt: string, schema: object): Promise<T> {
  const response = await withRetry(() =>
    client.models.generateContent({ model, contents: prompt, config: { temperature: 0, responseMimeType: "application/json", responseSchema: schema } }),
  );
  if (!response.text?.trim()) throw new Error(`${model} returned no text`);
  return JSON.parse(response.text) as T;
}

export async function generateText(client: GenClient, model: string, prompt: string): Promise<string> {
  const response = await withRetry(() => client.models.generateContent({ model, contents: prompt, config: { temperature: 0 } }));
  if (!response.text?.trim()) throw new Error(`${model} returned no text`);
  return response.text.trim();
}

export async function* streamText(client: GenClient, model: string, system: string, prompt: string): AsyncGenerator<string> {
  const stream = await withRetry(() =>
    client.models.generateContentStream({ model, contents: prompt, config: { systemInstruction: system, temperature: 0.1 } }),
  );
  for await (const chunk of stream) {
    if (chunk.text) yield chunk.text;
  }
}
```

- [ ] **Step 5: Write `web/lib/rag/rerank.ts`**

```ts
import type { Candidate } from "@/lib/rag/retrieve";
import { generateJson, type GenClient } from "@/lib/rag/genai";

export const KEEP = 8;
export const MIN_RELEVANCE = 2;
const SNIPPET = 700;
const SCHEMA = {
  type: "object",
  properties: {
    scores: {
      type: "array",
      items: { type: "object", properties: { id: { type: "integer" }, score: { type: "integer" } }, required: ["id", "score"] },
    },
  },
  required: ["scores"],
};

export function rerankPrompt(question: string, candidates: Candidate[]): string {
  const passages = candidates
    .map((c) => `[${c.id}] ${c.docTitle} › ${c.sectionPath} (p.${c.pageStart}, ${c.kind})\n${c.content.slice(0, SNIPPET)}`)
    .join("\n\n");
  return (
    "Score how well each passage answers the question: 3 = answers it directly, 2 = contains needed facts, " +
    "1 = related but not useful, 0 = unrelated. Score every passage by its [id].\n\n" +
    `Question: ${question}\n\nPassages:\n\n${passages}`
  );
}

/** One call scores all candidates 0–3 (spec §6.4). */
export async function rerank(client: GenClient, model: string, question: string, candidates: Candidate[]): Promise<Map<number, number>> {
  const answer = await generateJson<{ scores: { id: number; score: number }[] }>(client, model, rerankPrompt(question, candidates), SCHEMA);
  return new Map(answer.scores.map(({ id, score }) => [id, Math.max(0, Math.min(3, Math.round(score)))]));
}

/** Best-scored first (ties keep search order); only passages scoring ≥ MIN_RELEVANCE, at most KEEP. */
export function selectRelevant(candidates: Candidate[], scores: Map<number, number>): (Candidate & { relevance: number })[] {
  return candidates
    .map((c) => ({ ...c, relevance: scores.get(c.id) ?? 0 }))
    .filter((c) => c.relevance >= MIN_RELEVANCE)
    .sort((a, b) => b.relevance - a.relevance) // stable: ties keep search order
    .slice(0, KEEP);
}
```

- [ ] **Step 6: Run the tests**

Run: `cd web && npm test`
Expected: 42 passed, 2 skipped

- [ ] **Step 7: Commit**

```bash
git add web/lib/env.ts web/lib/env.test.ts web/lib/rag/genai.ts web/lib/rag/genai.test.ts web/lib/rag/rerank.ts web/lib/rag/rerank.test.ts web/lib/rag/testing.ts
git commit -m "Add reranking with the utility model"
```

---

### Task 4: Hybrid search and context assembly

**Files:**
- Create: `web/lib/rag/retrieve.ts`, `web/lib/rag/context.ts`
- Test: `web/lib/rag/retrieve.test.ts`, `web/lib/rag/context.test.ts`, `web/lib/rag/retrieve.int.test.ts`

**Interfaces:**
- Consumes: `Catalog` (Task 2); `getPool` (sub-project 1).
- Produces:
  - `type Candidate`, `type SearchArgs = { vector: number[]; question: string; docs: string[]; paths: string[]; limit?: number }`
  - `keywordQuery(question): string`, `hybridSearch(pool, args): Promise<Candidate[]>`, `loadCatalog(pool): Promise<Catalog>`
  - `CONTEXT_CHARS = 24_000`, `type SourceBlock` (`id`, `label`, `text`, `docSlug`, `sectionPath`, `pageStart`, `pageEnd`, `kind`, `chunkIds`), `loadNeighbours(pool, picked)`, `buildBlocks(picked, neighbours, budget?)`, `formatSources(blocks)`

- [ ] **Step 1: Write the failing tests.** Create `web/lib/rag/retrieve.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { keywordQuery } from "@/lib/rag/retrieve";

describe("keywordQuery", () => {
  it("ORs the question's words, keeping numbers and dropping short words", () => {
    expect(keywordQuery("How much did GPO procure in FY 2018 for agencies?")).toBe("how or much or did or gpo or procure or 2018 or for or agencies");
    expect(keywordQuery("ENERGY STAR 58% less")).toBe("energy or star or 58% or less");
  });
});
```

Create `web/lib/rag/context.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildBlocks, formatSources } from "@/lib/rag/context";
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
```

Create `web/lib/rag/retrieve.int.test.ts`. It uses a stored chunk's embedding as the query vector, so it needs no embedding API call:

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

Run: `cd web && npm test -- lib/rag/retrieve.test.ts lib/rag/context.test.ts`
Expected: FAIL with `Cannot find package '@/lib/rag/retrieve'` and `'@/lib/rag/context'`

- [ ] **Step 3: Write `web/lib/rag/retrieve.ts`**

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

export type SearchArgs = { vector: number[]; question: string; docs: string[]; paths: string[]; limit?: number };

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

export async function hybridSearch(pool: Pool, { vector, question, docs, paths, limit = 20 }: SearchArgs): Promise<Candidate[]> {
  const { rows } = await pool.query<Row>(HYBRID_SQL, [
    `[${vector.join(",")}]`, keywordQuery(question), docs, paths.length ? paths : null, limit,
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

- [ ] **Step 4: Write `web/lib/rag/context.ts`**

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

/** Text chunks next to each picked text chunk in the same section (seq ± 1), keyed by "section:seq". */
export async function loadNeighbours(pool: Pool, picked: Candidate[]): Promise<Map<string, Neighbour>> {
  const texts = picked.filter((c) => c.kind === "text");
  if (!texts.length) return new Map();
  const { rows } = await pool.query<Neighbour>(
    `SELECT c.id, c.section_id, c.seq, c.content, c.page_start, c.page_end
     FROM chunks c JOIN unnest($1::int[], $2::int[]) AS p(section_id, seq) ON c.section_id = p.section_id
     WHERE c.kind = 'text' AND c.seq IN (p.seq - 1, p.seq + 1)`,
    [texts.map((c) => c.sectionId), texts.map((c) => c.seq)],
  );
  return new Map(rows.map((row) => [`${row.section_id}:${row.seq}`, row]));
}

export function buildBlocks(picked: Candidate[], neighbours: Map<string, Neighbour>, budget = CONTEXT_CHARS): SourceBlock[] {
  const used = new Set(picked.map((c) => c.id));
  const blocks: SourceBlock[] = [];
  let size = 0;
  for (const c of picked) {
    const parts = [{ content: c.content, id: c.id, start: c.pageStart, end: c.pageEnd }];
    if (c.kind === "text") {
      for (const [seq, place] of [[c.seq - 1, "before"], [c.seq + 1, "after"]] as const) {
        const n = neighbours.get(`${c.sectionId}:${seq}`);
        if (n && !used.has(n.id)) {
          used.add(n.id);
          const part = { content: n.content, id: n.id, start: n.page_start, end: n.page_end };
          if (place === "before") parts.unshift(part);
          else parts.push(part);
        }
      }
    }
    const footnotes = c.footnotes.map((f) => `${f.n}. ${f.text}`).join("\n");
    const text = parts.map((p) => p.content).join("\n\n") + (footnotes ? `\n\nFootnotes:\n${footnotes}` : "");
    if (blocks.length && size + text.length > budget) break; // keep the best-ranked blocks within budget
    size += text.length;
    const pageStart = Math.min(...parts.map((p) => p.start));
    const pageEnd = Math.max(...parts.map((p) => p.end));
    const pages = pageStart === pageEnd ? `p.${pageStart}` : `p.${pageStart}-${pageEnd}`;
    const id = blocks.length + 1;
    blocks.push({
      id, label: `[S${id}] ${c.docTitle} › ${c.sectionPath} | ${pages} | ${c.kind}`, text,
      docSlug: c.docSlug, sectionPath: c.sectionPath, pageStart, pageEnd, kind: c.kind, chunkIds: parts.map((p) => p.id),
    });
  }
  return blocks;
}

export function formatSources(blocks: SourceBlock[]): string {
  return blocks.map((b) => `${b.label}\n${b.text}`).join("\n\n---\n\n");
}
```

- [ ] **Step 5: Run the unit and integration tests**

Run: `cd web && npm test`
Expected: 45 passed, 5 skipped (the 3 new live-DB tests skip without `RUN_INTEGRATION`)

Run: `cd web && npm run test:int`
Expected: 49 passed, 1 skipped (the 3 live-DB search tests now run)

- [ ] **Step 6: Commit**

```bash
git add web/lib/rag/retrieve.ts web/lib/rag/context.ts web/lib/rag/retrieve.test.ts web/lib/rag/context.test.ts web/lib/rag/retrieve.int.test.ts
git commit -m "Add hybrid search and context assembly"
```

---

### Task 5: Answer orchestrator

**Files:**
- Create: `web/lib/rag/prompt.ts`, `web/lib/rag/answer.ts`
- Test: `web/lib/rag/answer.test.ts`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `SYSTEM_PROMPT`, `userPrompt(question, sources)`, `condensePrompt(history, question)`
  - `type Turn`, `type ChatRequest = { question; scope: Scope; history: Turn[] }`, `type SourceInfo`, `type Debug`, `type ChatEvent` (`sources | text | done | error`), `type Deps` (`catalog`, `condense`, `embed`, `search`, `rerank`, `neighbours`, `generate`)
  - `refusal(scope, catalog, candidates): string`, `answer(request, deps, emit): Promise<void>`

- [ ] **Step 1: Write the failing tests.** Create `web/lib/rag/answer.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { answer, type ChatEvent, type Deps } from "@/lib/rag/answer";
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/rag/answer.test.ts`
Expected: FAIL with `Cannot find package '@/lib/rag/answer'`

- [ ] **Step 3: Write `web/lib/rag/prompt.ts`**

```ts
// Grounding rules for the answer model (spec §6.7).
export const SYSTEM_PROMPT = `You answer questions about reports using only the numbered sources you are given.

Rules:
- Use only facts stated in the sources. Never add outside knowledge.
- Cite every sentence with the source it comes from, in square brackets: [S2]. Several sources: [S1][S3].
- Copy numbers exactly as written, with their units and time periods (for example "FY 2018", "2026f", "346 kg CO2e").
- If the question is about one region, chapter or section, use only sources from it. Never mix facts from different regions or chapters unless the question asks for a comparison.
- Values marked "approximate, read from chart" are approximate: say so. Mention footnotes that qualify or limit a claim.
- If the sources answer only part of the question, answer that part and say what is missing. Do not fill gaps.
- Start with the direct answer, then add supporting detail. Be concise.`;

export function userPrompt(question: string, sources: string): string {
  return `Sources:\n\n${sources}\n\nQuestion: ${question}`;
}

export function condensePrompt(history: { role: string; content: string }[], question: string): string {
  const turns = history.map((turn) => `${turn.role === "user" ? "User" : "Assistant"}: ${turn.content}`).join("\n");
  return (
    "Rewrite the user's last question as a standalone question that can be understood without the conversation. " +
    "Keep names, regions, years and document references. Return only the question.\n\n" +
    `Conversation:\n${turns}\n\nLast question: ${question}`
  );
}
```

- [ ] **Step 4: Write `web/lib/rag/answer.ts`**

```ts
import { buildBlocks, formatSources, type SourceBlock } from "@/lib/rag/context";
import { SYSTEM_PROMPT, userPrompt } from "@/lib/rag/prompt";
import { selectRelevant } from "@/lib/rag/rerank";
import type { Candidate, SearchArgs } from "@/lib/rag/retrieve";
import { routeQuestion, type Catalog, type Route, type Scope } from "@/lib/rag/router";
import { verifyAnswer } from "@/lib/rag/verify";

export type Turn = { role: "user" | "assistant"; content: string };
export type ChatRequest = { question: string; scope: Scope; history: Turn[] };
export type SourceInfo = { id: number; doc: string; sectionPath: string; pageStart: number; pageEnd: number; kind: string; snippet: string };
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
  const closest = [...new Set(candidates.map((c) => `${c.docTitle} › ${c.sectionPath}`))].slice(0, 3);
  return `I couldn't find this in ${where}.` + (closest.length ? ` Closest sections: ${closest.join("; ")}.` : "");
}

function sourceInfo(block: SourceBlock, docTitle: string): SourceInfo {
  return {
    id: block.id, doc: docTitle, sectionPath: block.sectionPath, pageStart: block.pageStart, pageEnd: block.pageEnd,
    kind: block.kind, snippet: block.text.slice(0, 300),
  };
}

/** The whole question → answer flow (spec §6), emitting stream events. Every external call comes from `deps`. */
export async function answer(request: ChatRequest, deps: Deps, emit: (event: ChatEvent) => void): Promise<void> {
  const warnings: string[] = [];
  const question = await deps.condense(request.history, request.question);
  const catalog = await deps.catalog();
  const route = routeQuestion(question, request.scope, catalog);
  const candidates = await deps.search({ vector: await deps.embed(question), question, docs: route.docs, paths: route.paths });
  let scores: Map<number, number> | null = null;
  if (candidates.length) {
    try {
      scores = await deps.rerank(question, candidates);
    } catch {
      warnings.push("reranking failed; using search order");
    }
  }
  const picked = scores ? selectRelevant(candidates, scores) : candidates.slice(0, 8);
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
  const blocks = buildBlocks(picked, await deps.neighbours(picked));
  const titles = new Map(picked.map((c) => [c.docSlug, c.docTitle]));
  emit({ type: "sources", sources: blocks.map((b) => sourceInfo(b, titles.get(b.docSlug) ?? b.docSlug)) });
  let text = "";
  try {
    for await (const delta of deps.generate(SYSTEM_PROMPT, userPrompt(question, formatSources(blocks)))) {
      text += delta;
      emit({ type: "text", delta });
    }
  } catch (error) {
    emit({ type: "error", message: `answer generation failed: ${error instanceof Error ? error.message : String(error)}` });
    return;
  }
  warnings.push(...verifyAnswer(text, blocks));
  emit({ type: "done", warnings, debug });
}
```

- [ ] **Step 5: Run the tests**

Run: `cd web && npm test`
Expected: 50 passed, 5 skipped

- [ ] **Step 6: Commit**

```bash
git add web/lib/rag/prompt.ts web/lib/rag/answer.ts web/lib/rag/answer.test.ts
git commit -m "Add answer orchestrator"
```

---

### Task 6: `/api/chat` route

**Files:**
- Create: `web/lib/rag/request.ts`, `web/lib/rag/deps.ts`, `web/app/api/chat/route.ts`
- Test: `web/lib/rag/request.test.ts`
- Modify: `README.md` (an API section)

**Interfaces:**
- Consumes: everything above; `getEnv`, `getPool`, `embedQuery`, `getGenAI`, `errorMessage`, `evaluateSchema`, `readSchemaFacts` (sub-project 1).
- Produces:
  - `parseChatRequest(body): { ok: true; request } | { ok: false; error }`
  - `productionDeps(): Deps`. The catalog is cached per process. A failed condense returns the original question.
  - `POST /api/chat`. It returns 400 or 503 JSON, or a 200 NDJSON stream. The schema check runs once per process and is retried after a DB error.

- [ ] **Step 1: Write the failing tests.** Create `web/lib/rag/request.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseChatRequest } from "@/lib/rag/request";

describe("parseChatRequest", () => {
  it("fills defaults and trims the question", () => {
    expect(parseChatRequest({ question: "  Growth in SAR?  " })).toEqual({ ok: true, request: { question: "Growth in SAR?", scope: "all", history: [] } });
  });

  it("rejects empty, oversized or malformed requests with a readable reason", () => {
    expect(parseChatRequest({ question: " " })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x".repeat(1001) })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x", scope: "everything" })).toMatchObject({ ok: false, error: expect.stringContaining("scope") });
    expect(parseChatRequest(null)).toMatchObject({ ok: false });
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/rag/request.test.ts`
Expected: FAIL with `Cannot find package '@/lib/rag/request'`

- [ ] **Step 3: Write `web/lib/rag/request.ts`**

```ts
import { z } from "zod";
import type { ChatRequest } from "@/lib/rag/answer";

const ChatRequestSchema = z.object({
  question: z.string().trim().min(1).max(1000),
  scope: z.enum(["gpo", "gep", "imac", "all"]).default("all"),
  history: z.array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().max(4000) })).max(6).default([]),
});

export function parseChatRequest(body: unknown): { ok: true; request: ChatRequest } | { ok: false; error: string } {
  const result = ChatRequestSchema.safeParse(body);
  if (!result.success) {
    return { ok: false, error: result.error.issues.map((i) => `${i.path.join(".") || "body"}: ${i.message}`).join("; ") };
  }
  return { ok: true, request: result.data };
}
```

- [ ] **Step 4: Write `web/lib/rag/deps.ts`**

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

let catalog: Promise<Catalog> | undefined;

/** Production wiring: Postgres, Gemini embeddings, the utility model for helpers and the chat model for answers. */
export function productionDeps(): Deps {
  const env = getEnv();
  const client = getGenAI() as unknown as GenClient;
  const helper = utilityModel(env);
  return {
    catalog: () => (catalog ??= loadCatalog(getPool())),
    condense: async (history, question) => {
      if (!history.length) return question;
      try {
        return await generateText(client, helper, condensePrompt(history, question));
      } catch {
        return question; // a failed rewrite should not block the answer
      }
    },
    embed: embedQuery,
    search: (args) => hybridSearch(getPool(), args),
    rerank: (question, candidates) => rerank(client, helper, question, candidates),
    neighbours: (picked) => loadNeighbours(getPool(), picked),
    generate: (system, prompt) => streamText(client, env.GEMINI_CHAT_MODEL, system, prompt),
  };
}
```

- [ ] **Step 5: Write `web/app/api/chat/route.ts`**

```ts
import { NextResponse } from "next/server";
import { getEnv } from "@/lib/env";
import { answer, type ChatEvent } from "@/lib/rag/answer";
import { getPool } from "@/lib/rag/db";
import { productionDeps } from "@/lib/rag/deps";
import { errorMessage } from "@/lib/rag/health";
import { parseChatRequest } from "@/lib/rag/request";
import { evaluateSchema, readSchemaFacts } from "@/lib/rag/schema";

let schemaCheck: Promise<string[]> | undefined; // spec §7: refuse to answer if embeddings don't match the DB

function schemaProblems(): Promise<string[]> {
  const env = getEnv();
  schemaCheck ??= readSchemaFacts(getPool())
    .then((facts) => evaluateSchema(facts, { embedModel: env.GEMINI_EMBED_MODEL, embedDim: env.EMBED_DIM }))
    .catch((error) => {
      schemaCheck = undefined; // retry on the next request once the DB is back
      return [`Database unavailable: ${errorMessage(error)}`];
    });
  return schemaCheck;
}

export async function POST(req: Request) {
  const parsed = parseChatRequest(await req.json().catch(() => null));
  if (!parsed.ok) return NextResponse.json({ error: parsed.error }, { status: 400 });
  const problems = await schemaProblems();
  if (problems.length) return NextResponse.json({ error: problems.join("; ") }, { status: 503 });
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const emit = (event: ChatEvent) => controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
      try {
        await answer(parsed.request, productionDeps(), emit);
      } catch (error) {
        emit({ type: "error", message: errorMessage(error) });
      } finally {
        controller.close();
      }
    },
  });
  return new Response(stream, { headers: { "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store" } });
}
```

- [ ] **Step 6: Run the tests, type check and lint**

Run: `cd web && npm test && npm run test:int`
Expected: 52 passed, 5 skipped; then 56 passed, 1 skipped

Run: `cd web && npx next typegen && npx tsc --noEmit && npx eslint lib/rag app`
Expected: no errors. The only eslint output is the existing `_omit` warning in `lib/env.test.ts`.

- [ ] **Step 7: Check validation by hand.** These requests make no Gemini calls.

Run: `cd web && npm run dev` in a second terminal, then:
```bash
curl -s -w " %{http_code}\n" -X POST localhost:3100/api/chat -H 'content-type: application/json' -d '{"question":"  "}'
curl -s -w " %{http_code}\n" -X POST localhost:3100/api/chat -H 'content-type: application/json' -d '{"question":"x","scope":"everything"}'
```
Expected: two `{"error":"…"}` bodies, each with HTTP `400`.

- [ ] **Step 8: Live answer.** This needs the Gemini embedding quota (1 embedding) plus 2 generations. Skip it if the daily quota is spent and run it after the reset.

```bash
curl -sN -X POST localhost:3100/api/chat -H 'content-type: application/json' -d '{"question":"What is the growth outlook for South Asia?","scope":"gep"}'
```
Expected: a `{"type":"sources",…}` line whose sources sit under `Ch. 2 Regional Outlooks › South Asia`. Then `{"type":"text",…}` lines mentioning `6.3 percent` with `[S#]` citations, then `{"type":"done","warnings":[],…}`.

```bash
curl -sN -X POST localhost:3100/api/chat -H 'content-type: application/json' -d '{"question":"What is the iMac battery life?","scope":"imac"}'
```
Expected: a single text event starting `I couldn't find this in iMac Product Environmental Report`, then `done`. Stop the dev server.

- [ ] **Step 9: Document the API.** Append to `README.md`:

````markdown

## Chat API

`POST /api/chat` with `{"question": "...", "scope": "gpo" | "gep" | "imac" | "all", "history": [...]}` streams
newline-delimited JSON events: `sources`, then `text` deltas, then `done` (warnings, debug) — or `error`.

```bash
curl -sN -X POST localhost:3100/api/chat -H 'content-type: application/json' \
  -d '{"question":"What is the growth outlook for South Asia?","scope":"gep"}'
```

Answers use `GEMINI_CHAT_MODEL`; reranking and follow-up rewriting use `GEMINI_UTILITY_MODEL`
(default: `GEMINI_SUMMARY_MODEL`, then the chat model).
````

- [ ] **Step 10: Commit**

```bash
git add web/lib/rag/request.ts web/lib/rag/request.test.ts web/lib/rag/deps.ts web/app/api/chat/route.ts README.md
git commit -m "Add streaming chat API"
```

---

## Sub-project acceptance (spec §10, row 6)

- [ ] `cd web && npm test` → 52 passed, 5 skipped; `npm run test:int` → 56 passed, 1 skipped
- [ ] `npx next typegen && npx tsc --noEmit` is clean; eslint shows only the existing `_omit` warning
- [ ] `/api/chat` returns 400 for bad input and streams `sources` → `text` → `done`; South Asia answers cite only South Asia sources; an off-document question is refused without a generation call
- [ ] `git log --oneline main..HEAD` shows the spec update, the plan and 6 short task commits, with no trailers

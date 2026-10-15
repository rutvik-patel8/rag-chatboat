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

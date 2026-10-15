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

  it("passes the request's abort signal to the model call", async () => {
    const seen: { config?: Record<string, unknown> }[] = [];
    const stop = new AbortController();
    await rerank(fakeClient('{"scores":[]}', seen), "lite", "q", [candidate(1)], stop.signal);
    expect(seen[0].config?.abortSignal).toBe(stop.signal);
  });

  it("shows each passage's id, location and text", () => {
    const prompt = rerankPrompt("Growth in South Asia?", [candidate(7, { content: "Growth in SAR is expected to soften." })]);
    expect(prompt).toContain("[7] Global Economic Prospects › Ch. 2 Regional Outlooks › South Asia (p.96, text)\nGrowth in SAR is expected to soften.");
  });

  it("shows the whole passage, so facts late in a chunk can be scored", () => {
    const content = `${"Chemistry and longevity. ".repeat(50)}The stand uses 100 percent recycled aluminum.`;
    const prompt = rerankPrompt("What is the stand made of?", [candidate(7, { docTitle: "iMac Report", sectionPath: "", content })]);
    expect(prompt).toContain("[7] iMac Report (p.96, text)\n");
    expect(prompt).toContain("The stand uses 100 percent recycled aluminum.");
  });
});

describe("selectRelevant", () => {
  it("keeps passages scoring 2 or more, best first, at most 8", () => {
    const candidates = Array.from({ length: 12 }, (_, i) => candidate(i + 1));
    const scores = new Map(candidates.map((c) => [c.id, c.id === 5 ? 3 : c.id % 2 ? 2 : 1]));
    expect(selectRelevant(candidates, scores).map((c) => c.id)).toEqual([5, 1, 3, 7, 9, 11]);
  });
});

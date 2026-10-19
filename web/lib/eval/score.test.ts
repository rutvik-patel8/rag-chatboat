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
    expect(scoreAnswer(eap, { answer: "4.2 percent [S1][S3].", sources: [...ok, source(3, SAR, 96)], warnings: [] })).toMatchObject({ isolated: false, pass: false });
    expect(scoreAnswer(eap, { answer: "4.2 percent [S1], vs 6.3 percent [S1].", sources: ok, warnings: [] })).toMatchObject({ isolated: false, pass: false });
  });
});

describe("scoring edge cases from the review", () => {
  it("matches figures on number boundaries only", () => {
    const q = question({ must_contain: ["6.3%"], expected_pages: [96] });
    const src = [source(1, SAR, 96)];
    expect(scoreAnswer(q, { answer: "Growth is 16.3 percent [S1].", sources: src, warnings: [] }).facts).toBe(false);
    expect(scoreAnswer(q, { answer: "Growth is 6.3 percent [S1].", sources: src, warnings: [] }).facts).toBe(true);
    expect(scoreAnswer(question({ must_contain: ["375"], expected_pages: [96] }), { answer: "$375.7 million [S1]", sources: src, warnings: [] }).facts).toBe(true);
    expect(scoreAnswer(question({ must_contain: ["interest rate"], expected_pages: [96] }), { answer: "higher interest rates [S1]", sources: src, warnings: [] }).facts).toBe(true);
  });

  it("accepts a curly-apostrophe refusal but not a refusal that goes on to cite sources", () => {
    const trap = question({ type: "trap", scope: "gpo" });
    expect(scoreAnswer(trap, { answer: "I couldn’t find this in the documents.", sources: [], warnings: [] }).refused).toBe(true);
    const leaky = "I couldn't find this exactly. However, the FY 2022 budget was $117 million [S1].";
    expect(scoreAnswer(trap, { answer: leaky, sources: [source(1, "Budget", 14)], warnings: [] }).refused).toBe(false);
  });

  it("judges isolation on the sources the answer cites", () => {
    const eap = question({ type: "isolation", must_contain: ["4.2%"], expected_pages: [81], expected_section: EAP });
    const sources = [source(1, `${EAP} › Outlook`, 81), source(2, "Ch. 2 Regional Outlooks › Outlook", 73)];
    expect(scoreAnswer(eap, { answer: "EAP growth is 4.2 percent [S1].", sources, warnings: [] }).isolated).toBe(true);
    expect(scoreAnswer(eap, { answer: "EAP growth is 4.2 percent [S1][S2].", sources, warnings: [] }).isolated).toBe(false);
  });
});

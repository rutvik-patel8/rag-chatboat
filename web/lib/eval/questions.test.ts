import path from "node:path";
import { describe, expect, it } from "vitest";
import { loadQuestions, parseQuestions } from "@/lib/eval/questions";
import { scoreAnswer } from "@/lib/eval/score";
import type { SourceInfo } from "@/lib/rag/answer";

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

describe("question data against real answers (ablation run, 2026-10-15)", () => {
  const questions = loadQuestions(FILE);
  const get = (id: string) => questions.find((q) => q.id === id)!;
  const src = (id: number, sectionPath: string, pageStart: number, pageEnd = pageStart): SourceInfo => ({
    id, slug: "x", doc: "Doc", sectionPath, pageStart, pageEnd, kind: "text", snippet: "", text: "",
  });

  it("counts every page that states the fact: govinfo on pp4, 10, 22 and the ENERGY STAR endnote on p12", () => {
    const govinfo = scoreAnswer(get("gpo-govinfo"), {
      answer: "The new digital system that replaced FDsys is **govinfo** [S1][S2][S3]. FDsys was retired and replaced by govinfo in December 2018 [S1][S2][S3].",
      sources: [src(1, "About GPO › Technology Transformation", 9, 10), src(2, "GPO and Open, Transparent Government › govinfo", 22), src(3, "SECTION I › Programs", 39)],
      warnings: [],
    });
    expect(govinfo).toMatchObject({ hit: true, pass: true });
    const energy = scoreAnswer(get("imac-energy-star"), {
      answer: "iMac uses 58 percent less energy than the requirement for ENERGY STAR [S1][S2].",
      sources: [src(1, "Use", 7), src(2, "Endnotes", 12)],
      warnings: [],
    });
    expect(energy.citation).toBe(1);
  });

  it("checks a distinctive fact for the broad questions and exact figures elsewhere", () => {
    expect(get("gep-chapter-3").must_contain).toEqual(["interest rate", "non-linear"]);
    expect(get("gep-exec-risks").must_contain).toEqual(["downside", "1.3%"]);
    expect(get("imac-stand").must_contain).toEqual(["aluminum", "100%"]);
  });

  it("forbids only South Asia figures absent from the East Asia pages, and pins the annex question to its page", () => {
    expect(get("isolation-eap").must_not_contain).toEqual(["6.3%", "7%"]);
    expect(get("gep-annex-bhutan").expected_pages).toEqual([189]);
    expect(get("gep-annex-bhutan").expected_section).toBeUndefined();
  });
});

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

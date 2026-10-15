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

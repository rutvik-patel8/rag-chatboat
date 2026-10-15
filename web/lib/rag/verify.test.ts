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

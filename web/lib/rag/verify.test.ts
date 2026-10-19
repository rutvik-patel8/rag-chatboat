import { describe, expect, it } from "vitest";
import { numbersIn } from "@/lib/rag/numbers";
import { verifyAnswer } from "@/lib/rag/verify";

describe("numbersIn", () => {
  it("compares figures by value and ignores citations and digits inside words", () => {
    expect(numbersIn("GPO staff visited 166 libraries [S1] and distributed 802,834 copies in FY 2018")).toEqual(["166", "802834", "2018"]);
    expect(numbersIn("growth of 6.30 percent, a −0.1 revision, CO2e and M4 chips")).toEqual(["6.3", "-0.1"]);
  });

  it("ignores numbered-list markers", () => {
    expect(numbersIn("The main risks are:\n1. Trade tensions [S1]\n2) Debt of 4.4 percent [S2]\n  3. Conflict")).toEqual(["4.4"]);
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

describe("verifyAnswer on a refusal", () => {
  it("does not warn when the model says the sources do not answer the question", () => {
    expect(verifyAnswer("I couldn't find this in the documents.", [{ id: 1, label: "[S1] GEP", text: "Growth 6.3" }])).toEqual([]);
  });
});

describe("verifyAnswer with grouped citations", () => {
  it("reads [S1, S2] as citing both sources", () => {
    const sources = [{ id: 1, label: "[S1] GEP", text: "Growth 4.2" }, { id: 2, label: "[S2] GEP", text: "China 4.4" }];
    expect(verifyAnswer("EAP 4.2 and China 4.4 [S1, S2].", sources)).toEqual([]);
    expect(verifyAnswer("China 4.4 [S1; S3].", sources)).toEqual(["cites unknown sources: S3", "unverified figures: 4.4"]);
  });
});

describe("verifyAnswer with references to footnotes, tables and chapters", () => {
  it("treats 'footnote 18', 'Table 2.9' or 'Chapter 3' as labels, not figures (real answer, eval 2026-10-19)", () => {
    const sources = [{
      id: 1, label: "[S1] iMac Report › Use | p.7 | text",
      text: "iMac uses 58 percent less energy than the requirement for ENERGY STAR.18\n\nFootnotes:\n18. Energy consumption values are based on the ENERGY STAR Program Requirements.",
    }];
    const answer = "iMac uses 58 percent less energy than the requirement for ENERGY STAR [S1]. Footnote 18 states that the values are based on the ENERGY STAR Program Requirements [S1].";
    expect(verifyAnswer(answer, sources)).toEqual([]);
    expect(numbersIn("See Table 2.9, Box 1.1, Figure 2.3.A, endnote 10 and Chapter 3: growth is 6.3 percent.")).toEqual(["6.3"]);
    expect(verifyAnswer("Growth is 9.9 percent, per footnote 18 [S1].", sources)).toEqual(["unverified figures: 9.9"]);
  });
});

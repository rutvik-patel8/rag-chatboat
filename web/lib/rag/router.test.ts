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

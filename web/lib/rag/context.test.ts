import { describe, expect, it } from "vitest";
import { buildBlocks, formatSources, sectionLabel } from "@/lib/rag/context";
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

describe("sectionLabel", () => {
  it("names the document alone for text outside any section", () => {
    expect(sectionLabel("iMac Report", "")).toBe("iMac Report");
    expect(sectionLabel("GEP", "Ch. 3")).toBe("GEP › Ch. 3");
    const [block] = buildBlocks([candidate(5, { docTitle: "iMac Report", sectionPath: "" })], new Map());
    expect(block.label).toBe("[S1] iMac Report | p.96 | text");
  });
});

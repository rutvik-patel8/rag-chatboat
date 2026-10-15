import { describe, expect, it } from "vitest";
import { inlineParts, pageLabel, parseAnswer, pdfHref } from "@/lib/chat/format";

const known = new Set([1, 2, 3]);

describe("inlineParts", () => {
  it("turns citations into chips, including [S1][S3] and [S1, S2]", () => {
    expect(inlineParts("Growth is 6.3 percent [S1][S3]. Debt rose [S1, S2].", known)).toEqual([
      { type: "text", text: "Growth is 6.3 percent ", strong: false },
      { type: "cite", id: 1 }, { type: "cite", id: 3 },
      { type: "text", text: ". Debt rose ", strong: false },
      { type: "cite", id: 1 }, { type: "cite", id: 2 },
      { type: "text", text: ".", strong: false },
    ]);
  });

  it("keeps unknown citations and other brackets as plain text, and marks bold runs", () => {
    expect(inlineParts("**Risks:** see [S9] and [note]", known)).toEqual([
      { type: "text", text: "Risks:", strong: true },
      { type: "text", text: " see [S9] and [note]", strong: false },
    ]);
  });
});

describe("parseAnswer", () => {
  it("splits paragraphs and bullet or numbered list items", () => {
    const blocks = parseAnswer("Growth softens [S1].\nIt recovers later.\n\n- **India:** 6.6 percent [S2]\n2. Debt [S3]", known);
    expect(blocks.map((b) => (b.type === "li" ? `li ${b.marker}` : "p"))).toEqual(["p", "li •", "li 2."]);
    expect(blocks[0].parts[0]).toEqual({ type: "text", text: "Growth softens ", strong: false });
    expect(blocks[0].parts.at(-1)).toEqual({ type: "text", text: ". It recovers later.", strong: false });
  });
});

describe("source links", () => {
  it("labels pages and links to the PDF page", () => {
    expect(pageLabel({ pageStart: 96, pageEnd: 96 })).toBe("p.96");
    expect(pageLabel({ pageStart: 95, pageEnd: 97 })).toBe("pp.95–97");
    expect(pdfHref({ slug: "imac", pageStart: 7 })).toBe("/api/pdf/imac#page=7");
  });
});

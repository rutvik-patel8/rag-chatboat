import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AssistantBubble } from "@/components/AssistantBubble";
import { SourcesDrawer } from "@/components/SourcesDrawer";
import { emptyAnswer, type AssistantMessage } from "@/lib/chat/thread";
import type { SourceInfo } from "@/lib/rag/answer";

const source: SourceInfo = {
  id: 1, slug: "gep", doc: "GEP", sectionPath: "Ch. 2 Regional Outlooks › South Asia", pageStart: 96, pageEnd: 97, kind: "text",
  snippet: "Growth", text: "Growth in SAR is expected to soften to 6.3 percent.",
};
const noop = () => {};
const bubble = (message: Partial<AssistantMessage>, showDebug = false) =>
  renderToStaticMarkup(<AssistantBubble message={{ ...emptyAnswer(), ...message }} showDebug={showDebug} onCite={noop} />);

describe("AssistantBubble", () => {
  it("renders citation chips for known sources, plain text for unknown ones, and the source list", () => {
    const html = bubble({ status: "done", text: "Growth softens to 6.3 percent [S1], not [S9].", sources: [source] });
    expect(html).toContain(">S1</button>");
    expect(html).toContain("not [S9].");
    expect(html).toContain("GEP › Ch. 2 Regional Outlooks › South Asia · pp.96–97");
  });

  it("shows a warning badge, errors, stopped answers and progress", () => {
    expect(bubble({ status: "done", text: "6.4 percent [S1]", sources: [source], warnings: ["unverified figures: 6.4"] })).toContain("⚠ Unverified figures: 6.4");
    expect(bubble({ status: "error", error: "answer generation failed: 429" })).toContain('role="alert"');
    expect(bubble({ status: "stopped", text: "Half" })).toContain("Stopped.");
    expect(bubble({})).toContain("Searching the documents…");
  });

  it("shows retrieval details only when asked", () => {
    const debug = { question: "q", route: { docs: ["gep"], paths: ["Ch. 2 Regional Outlooks › South Asia"] }, candidates: [{ chunkId: 7924, vecRank: 1, kwRank: 3, rrf: 0.0323, rerank: 3 }] };
    expect(bubble({ status: "done", text: "x", debug })).not.toContain("Retrieval");
    const html = bubble({ status: "done", text: "x", debug }, true);
    expect(html).toContain("Section filter: Ch. 2 Regional Outlooks › South Asia");
    expect(html).toContain("<td>7924</td><td>1</td><td>3</td><td>0.0323</td><td>3</td>");
  });
});

describe("SourcesDrawer", () => {
  it("lists the clicked source first with its text and a link to its PDF page", () => {
    const other = { ...source, id: 2, slug: "imac", doc: "iMac", sectionPath: "", pageStart: 7, pageEnd: 7, text: "Stand: 100 percent recycled aluminum." };
    const html = renderToStaticMarkup(<SourcesDrawer sources={[source, other]} active={2} onClose={noop} />);
    expect(html.indexOf("S2 · iMac")).toBeLessThan(html.indexOf("S1 · GEP"));
    expect(html).toContain('href="/api/pdf/imac#page=7" target="_blank"');
    expect(html).toContain("Stand: 100 percent recycled aluminum.");
  });
});

describe("Chat", () => {
  it("starts with the document selector, the retrieval toggle and the brief's questions", async () => {
    const { Chat, EXAMPLES } = await import("@/components/Chat");
    const html = renderToStaticMarkup(<Chat />);
    for (const label of ["All documents", "GPO 2018", "World Bank GEP", "iMac report", "Show retrieval"]) expect(html).toContain(label);
    expect(html).toContain('aria-checked="true"');
    EXAMPLES.all.forEach((q) => expect(html).toContain(q.replace(/'/g, "&#x27;")));
    expect(Object.values(EXAMPLES).flat()).toHaveLength(12);
  });
});

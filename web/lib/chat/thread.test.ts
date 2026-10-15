import { describe, expect, it } from "vitest";
import { applyEvent, emptyAnswer, historyFor, type Message } from "@/lib/chat/thread";
import type { SourceInfo } from "@/lib/rag/answer";

const source: SourceInfo = {
  id: 1, slug: "gep", doc: "GEP", sectionPath: "Ch. 2", pageStart: 96, pageEnd: 97, kind: "text", snippet: "s", text: "t",
};

describe("applyEvent", () => {
  it("builds an answer from sources, text deltas and done", () => {
    let m = emptyAnswer();
    m = applyEvent(m, { type: "sources", sources: [source] });
    m = applyEvent(m, { type: "text", delta: "Growth " });
    m = applyEvent(m, { type: "text", delta: "is 6.3 percent [S1]." });
    m = applyEvent(m, { type: "done", warnings: ["unverified figures: 7"], debug: { question: "q", route: { docs: ["gep"], paths: [] }, candidates: [] } });
    expect(m).toMatchObject({ status: "done", text: "Growth is 6.3 percent [S1].", sources: [source], warnings: ["unverified figures: 7"] });
  });

  it("keeps the sources and partial text when an error arrives", () => {
    const m = applyEvent(applyEvent(applyEvent(emptyAnswer(), { type: "sources", sources: [source] }), { type: "text", delta: "Part" }), {
      type: "error", message: "answer generation failed: 429",
    });
    expect(m).toMatchObject({ status: "error", text: "Part", sources: [source], error: "answer generation failed: 429" });
  });
});

describe("historyFor", () => {
  const done = (text: string): Message => ({ ...emptyAnswer(), status: "done", text });
  const ask = (text: string): Message => ({ role: "user", text, scope: "all" });

  it("sends completed pairs only, newest 6 turns", () => {
    const messages: Message[] = [
      ask("q1"), done("a1"), ask("q2"), done("a2"), ask("q3"), { ...emptyAnswer(), status: "error", error: "x" },
      ask("q4"), { ...emptyAnswer(), status: "stopped", text: "half" }, ask("q5"), done("a5"), ask("q6"), done("a6"),
    ];
    expect(historyFor(messages).map((t) => t.content)).toEqual(["q2", "a2", "q5", "a5", "q6", "a6"]);
  });
});

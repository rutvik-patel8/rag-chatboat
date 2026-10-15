import { describe, expect, it } from "vitest";
import { parseChatRequest } from "@/lib/rag/request";

describe("parseChatRequest", () => {
  it("fills defaults and trims the question", () => {
    expect(parseChatRequest({ question: "  Growth in SAR?  " })).toEqual({ ok: true, request: { question: "Growth in SAR?", scope: "all", history: [] } });
  });

  it("keeps the last 6 turns of a long conversation, including long answers", () => {
    const history = Array.from({ length: 8 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: `turn ${i} ${"x".repeat(i === 7 ? 5000 : 0)}` }));
    const parsed = parseChatRequest({ question: "and then?", history });
    expect(parsed.ok && parsed.request.history.map((t) => t.content.slice(0, 6))).toEqual(["turn 2", "turn 3", "turn 4", "turn 5", "turn 6", "turn 7"]);
  });

  it("rejects empty, oversized or malformed requests with a readable reason", () => {
    expect(parseChatRequest({ question: " " })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x".repeat(1001) })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x", scope: "everything" })).toMatchObject({ ok: false, error: expect.stringContaining("scope") });
    expect(parseChatRequest(null)).toMatchObject({ ok: false });
  });
});

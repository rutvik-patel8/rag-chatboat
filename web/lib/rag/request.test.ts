import { describe, expect, it } from "vitest";
import { parseChatRequest } from "@/lib/rag/request";

describe("parseChatRequest", () => {
  it("fills defaults and trims the question", () => {
    expect(parseChatRequest({ question: "  Growth in SAR?  " })).toEqual({ ok: true, request: { question: "Growth in SAR?", scope: "all", history: [] } });
  });

  it("rejects empty, oversized or malformed requests with a readable reason", () => {
    expect(parseChatRequest({ question: " " })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x".repeat(1001) })).toMatchObject({ ok: false });
    expect(parseChatRequest({ question: "x", scope: "everything" })).toMatchObject({ ok: false, error: expect.stringContaining("scope") });
    expect(parseChatRequest(null)).toMatchObject({ ok: false });
  });
});

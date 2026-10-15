import { describe, expect, it } from "vitest";
import { generateJson, streamText, type GenClient } from "@/lib/rag/genai";

describe("genai helpers", () => {
  it("parses JSON answers and rejects empty ones", async () => {
    const client = (text: string): GenClient => ({
      models: { generateContent: async () => ({ text }), generateContentStream: async () => (async function* () {})() },
    });
    expect(await generateJson(client('{"a":1}'), "m", "p", {})).toEqual({ a: 1 });
    await expect(generateJson(client(""), "m", "p", {})).rejects.toThrow("returned no text");
  });

  it("streams text deltas with the system prompt at low temperature", async () => {
    const seen: { config?: Record<string, unknown> }[] = [];
    const client: GenClient = {
      models: {
        generateContent: async () => ({ text: "" }),
        async generateContentStream(req) {
          seen.push(req);
          return (async function* () {
            yield { text: "Hello " };
            yield {};
            yield { text: "world" };
          })();
        },
      },
    };
    const parts: string[] = [];
    for await (const part of streamText(client, "flash", "rules", "question")) parts.push(part);
    expect(parts).toEqual(["Hello ", "world"]);
    expect(seen[0].config).toEqual({ systemInstruction: "rules", temperature: 0.1 });
  });
});

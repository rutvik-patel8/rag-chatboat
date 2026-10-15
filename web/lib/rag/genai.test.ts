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
    expect(seen[0].config).toMatchObject({ systemInstruction: "rules", temperature: 0.1 });
  });
});

describe("streamText limits", () => {
  const client = (stream: GenClient["models"]["generateContentStream"], seen: unknown[] = []): GenClient => ({
    models: {
      async generateContent(req) {
        seen.push(req);
        return { text: "{}" };
      },
      generateContentStream: stream,
    },
  });
  const drain = async (it: AsyncIterable<string>) => {
    for await (const _ of it); // eslint-disable-line @typescript-eslint/no-unused-vars
  };

  it("gives up when the model sends nothing before the first-chunk deadline", async () => {
    const started = Date.now();
    const hung = client(() => new Promise(() => {}));
    await expect(drain(streamText(hung, "flash", "s", "p", { firstChunkMs: 50 }))).rejects.toThrow("flash sent nothing for 0.05 s");
    expect(Date.now() - started).toBeLessThan(1000);
  });

  it("tries an overloaded model at most twice", async () => {
    let calls = 0;
    const busy = client(async () => {
      calls++;
      throw Object.assign(new Error("busy"), { status: 503 });
    });
    await expect(drain(streamText(busy, "flash", "s", "p", { baseDelayMs: 1 }))).rejects.toThrow("busy");
    expect(calls).toBe(2);
  });

  it("passes the caller's abort signal to every call and never starts once stopped", async () => {
    const seen: { config?: { abortSignal?: AbortSignal } }[] = [];
    const controller = new AbortController();
    const c = client(async (req) => {
      seen.push(req);
      return (async function* () { yield { text: "x" }; })();
    }, seen);
    await generateJson(c, "lite", "p", {}, { signal: controller.signal });
    expect(seen[0].config?.abortSignal).toBe(controller.signal);
    controller.abort(new Error("client disconnected"));
    await expect(drain(streamText(c, "flash", "s", "p", { signal: controller.signal }))).rejects.toThrow("client disconnected");
    expect(seen).toHaveLength(1);
  });
});

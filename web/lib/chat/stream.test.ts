import { describe, expect, it } from "vitest";
import type { ChatEvent } from "@/lib/rag/answer";
import { askChat, readEvents } from "@/lib/chat/stream";

/** A body that delivers the given byte chunks one read at a time. */
function body(...chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(typeof c === "string" ? encoder.encode(c) : c));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<ChatEvent[]> {
  const events: ChatEvent[] = [];
  for await (const event of readEvents(stream)) events.push(event);
  return events;
}

async function ask(response: Response | Error, signal?: AbortSignal): Promise<ChatEvent[]> {
  const events: ChatEvent[] = [];
  const fetchFn = (async () => {
    if (response instanceof Error) throw response;
    return response;
  }) as typeof fetch;
  await askChat({ question: "q", scope: "all", history: [] }, (e) => events.push(e), { fetchFn, signal });
  return events;
}

describe("readEvents", () => {
  it("handles lines split across chunks, several lines per chunk, split characters and a final line without newline", async () => {
    const euro = new TextEncoder().encode('{"type":"text","delta":"€"}\n');
    const events = await collect(body('{"type":"text","de', 'lta":"a"}\n{"type":"text","delta":"b"}\n\n', euro.slice(0, 25), euro.slice(25), '{"type":"text","delta":"c"}'));
    expect(events.map((e) => (e as { delta: string }).delta)).toEqual(["a", "b", "€", "c"]);
  });
});

describe("askChat", () => {
  it("passes every event through", async () => {
    const events = await ask(new Response('{"type":"text","delta":"Hi"}\n{"type":"done","warnings":[],"debug":null}\n'));
    expect(events.map((e) => e.type)).toEqual(["text", "done"]);
  });

  it("turns HTTP errors, network failures and broken or truncated streams into one error event", async () => {
    expect(await ask(Response.json({ error: "question: Too small" }, { status: 400 }))).toEqual([{ type: "error", message: "question: Too small" }]);
    expect(await ask(new Response("oops", { status: 502 }))).toEqual([{ type: "error", message: "Request failed (HTTP 502)." }]);
    expect(await ask(new TypeError("fetch failed"))).toEqual([{ type: "error", message: "Could not reach the server." }]);
    expect((await ask(new Response('{"type":"text","delta":"a"}\nnot json\n'))).at(-1)).toEqual({ type: "error", message: "The answer stream was interrupted." });
    expect((await ask(new Response('{"type":"text","delta":"a"}\n'))).at(-1)).toEqual({ type: "error", message: "The answer stream ended early." });
  });

  it("ends quietly when the caller stops the request", async () => {
    const controller = new AbortController();
    controller.abort();
    expect(await ask(new DOMException("aborted", "AbortError") as unknown as Error, controller.signal)).toEqual([]);
  });
});

import { describe, expect, it } from "vitest";
import type { ChatEvent } from "@/lib/rag/answer";
import { ndjsonStream } from "@/lib/rag/ndjson";

async function readAll(stream: ReadableStream<Uint8Array>): Promise<string> {
  return new Response(stream).text();
}

describe("ndjsonStream", () => {
  it("writes one JSON event per line and turns a thrown error into an error event", async () => {
    const text = await readAll(ndjsonStream(async (emit) => {
      emit({ type: "text", delta: "Hi" });
      throw new Error("boom");
    }));
    expect(text).toBe('{"type":"text","delta":"Hi"}\n{"type":"error","message":"boom"}\n');
  });

  it("stops the answer when the client cancels, without writing to the closed stream", async () => {
    let release!: () => void;
    const paused = new Promise<void>((resolve) => (release = resolve));
    let afterCancel: unknown;
    const stream = ndjsonStream(async (emit) => {
      emit({ type: "text", delta: "first" });
      await paused;
      try {
        emit({ type: "text", delta: "second" } as ChatEvent);
      } catch (error) {
        afterCancel = error;
        throw error;
      }
    });
    const reader = stream.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe('{"type":"text","delta":"first"}\n');
    await reader.cancel();
    release();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(afterCancel).toBeInstanceOf(Error);
  });
});

describe("ndjsonStream cancel", () => {
  it("aborts the signal given to the answer, so in-flight Gemini calls stop", async () => {
    let signal!: AbortSignal;
    const stream = ndjsonStream(async (emit, s) => {
      signal = s;
      emit({ type: "text", delta: "first" });
      await new Promise((resolve) => s.addEventListener("abort", resolve));
    });
    const reader = stream.getReader();
    await reader.read();
    await reader.cancel();
    expect(signal.aborted).toBe(true);
  });
});

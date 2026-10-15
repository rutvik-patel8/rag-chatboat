import type { ChatEvent } from "@/lib/rag/answer";
import { errorMessage } from "@/lib/rag/health";

/**
 * Stream `run`'s events as NDJSON, one JSON object per line. If the client goes away (Stop button, closed tab),
 * `signal` aborts in-flight Gemini calls and the next `emit` throws, so the answer stops instead of spending
 * more calls, and nothing is written to the closed stream.
 */
export function ndjsonStream(
  run: (emit: (event: ChatEvent) => void, signal: AbortSignal) => Promise<void>,
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const stop = new AbortController(); // aborts in-flight Gemini calls when the client goes away
  let closed = false;
  return new ReadableStream({
    async start(controller) {
      const emit = (event: ChatEvent) => {
        if (closed) throw new Error("client disconnected");
        controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
      };
      try {
        await run(emit, stop.signal);
      } catch (error) {
        if (!closed) emit({ type: "error", message: errorMessage(error) });
      } finally {
        if (!closed) controller.close();
      }
    },
    cancel() {
      closed = true;
      stop.abort(new Error("client disconnected"));
    },
  });
}

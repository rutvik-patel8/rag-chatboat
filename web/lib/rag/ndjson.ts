import type { ChatEvent } from "@/lib/rag/answer";
import { errorMessage } from "@/lib/rag/health";

/**
 * Stream `run`'s events as NDJSON, one JSON object per line. If the client goes away (Stop button, closed tab),
 * the next `emit` throws, so the answer stops instead of spending more Gemini calls, and nothing is written
 * to the closed stream.
 */
export function ndjsonStream(run: (emit: (event: ChatEvent) => void) => Promise<void>): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let closed = false;
  return new ReadableStream({
    async start(controller) {
      const emit = (event: ChatEvent) => {
        if (closed) throw new Error("client disconnected");
        controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
      };
      try {
        await run(emit);
      } catch (error) {
        if (!closed) emit({ type: "error", message: errorMessage(error) });
      } finally {
        if (!closed) controller.close();
      }
    },
    cancel() {
      closed = true;
    },
  });
}

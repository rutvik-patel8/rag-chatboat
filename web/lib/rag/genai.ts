import { withRetry } from "@/lib/rag/gemini";

/** The slice of @google/genai used for text generation; tests pass fakes with the same shape. */
export type GenClient = {
  models: {
    generateContent(req: { model: string; contents: string; config?: Record<string, unknown> }): Promise<{ text?: string }>;
    generateContentStream(req: { model: string; contents: string; config?: Record<string, unknown> }): Promise<AsyncIterable<{ text?: string }>>;
  };
};

type CallOptions = { signal?: AbortSignal };

export const FIRST_CHUNK_MS = 30_000; // an overloaded model can sit ~60 s before its 503; fail sooner
export const STREAM_ATTEMPTS = 2; // the user is waiting: one retry, not three

export async function generateJson<T>(client: GenClient, model: string, prompt: string, schema: object, { signal }: CallOptions = {}): Promise<T> {
  const config = { temperature: 0, responseMimeType: "application/json", responseSchema: schema, abortSignal: signal };
  const response = await withRetry(() => client.models.generateContent({ model, contents: prompt, config }), { signal });
  if (!response.text?.trim()) throw new Error(`${model} returned no text`);
  return JSON.parse(response.text) as T;
}

export async function generateText(client: GenClient, model: string, prompt: string, { signal }: CallOptions = {}): Promise<string> {
  const config = { temperature: 0, abortSignal: signal };
  const response = await withRetry(() => client.models.generateContent({ model, contents: prompt, config }), { signal });
  if (!response.text?.trim()) throw new Error(`${model} returned no text`);
  return response.text.trim();
}

/** Reject as soon as `signal` aborts, even if the SDK call ignores it. */
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", onAbort)); // always observed
    if (signal.aborted) return onAbort();
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

type StreamOptions = CallOptions & { firstChunkMs?: number; attempts?: number; baseDelayMs?: number };

/**
 * Stream the answer. The first chunk must arrive within `firstChunkMs` (retries included); after that the answer
 * may take as long as it needs. `signal` (the client went away) cancels the call and any retry.
 */
export async function* streamText(client: GenClient, model: string, system: string, prompt: string, options: StreamOptions = {}): AsyncGenerator<string> {
  const { signal, firstChunkMs = FIRST_CHUNK_MS, attempts = STREAM_ATTEMPTS, baseDelayMs } = options;
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(new Error(`${model} sent nothing for ${firstChunkMs / 1000} s`)), firstChunkMs);
  const abort = signal ? AbortSignal.any([signal, deadline.signal]) : deadline.signal;
  try {
    const config = { systemInstruction: system, temperature: 0.1, abortSignal: abort };
    const stream = await untilAborted(
      withRetry(() => client.models.generateContentStream({ model, contents: prompt, config }), { attempts, baseDelayMs, signal: abort }),
      abort,
    );
    const chunks = stream[Symbol.asyncIterator]();
    let next = await untilAborted(chunks.next(), abort);
    clearTimeout(timer); // the model is answering: no deadline for the rest
    for (; !next.done; next = await chunks.next()) {
      if (next.value.text) yield next.value.text;
    }
  } finally {
    clearTimeout(timer);
  }
}

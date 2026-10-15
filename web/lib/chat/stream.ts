import type { ChatEvent, ChatRequest } from "@/lib/rag/answer";

/** Parse an NDJSON body into events. Lines may arrive split across chunks or several per chunk. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder(); // stream mode keeps multi-byte characters split across chunks intact
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) yield JSON.parse(line) as ChatEvent;
    }
  }
  if (buffer.trim()) yield JSON.parse(buffer) as ChatEvent;
}

type AskOptions = { signal?: AbortSignal; fetchFn?: typeof fetch };

/**
 * POST a question to /api/chat and hand every event to `onEvent`. Every failure (HTTP 400/503, network, a broken
 * or truncated stream) arrives as one final `error` event, so the caller only handles events. A stop by the
 * caller (`signal`) ends quietly.
 */
export async function askChat(request: ChatRequest, onEvent: (event: ChatEvent) => void, options: AskOptions = {}): Promise<void> {
  const { signal, fetchFn = fetch } = options;
  const fail = (message: string) => {
    if (!signal?.aborted) onEvent({ type: "error", message });
  };
  let res: Response;
  try {
    res = await fetchFn("/api/chat", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(request), signal,
    });
  } catch {
    return fail("Could not reach the server.");
  }
  if (!res.ok || !res.body) {
    const data = (await res.json().catch(() => null)) as { error?: string } | null;
    return fail(data?.error ?? `Request failed (HTTP ${res.status}).`);
  }
  let finished = false;
  try {
    for await (const event of readEvents(res.body)) {
      finished ||= event.type === "done" || event.type === "error";
      onEvent(event);
    }
  } catch {
    return fail("The answer stream was interrupted.");
  }
  if (!finished) fail("The answer stream ended early.");
}

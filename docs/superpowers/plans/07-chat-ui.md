# Chat UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A browser chat over the three documents. It has a document selector, streamed answers with `[S#]` citation chips, a sources drawer with "Open page" PDF links, a "Show retrieval" toggle and a badge for unverified figures.

**Architecture:**
- **Pure client logic** lives in `lib/chat/` and is unit-tested: the NDJSON reader and request helper, thread state, and answer formatting.
- **Presentational components** live in `components/` and are tested by rendering to static HTML with `react-dom/server` (no jsdom).
- **One client component**, `components/Chat.tsx`, holds the state and is checked in a real browser.
- **Server changes:**
  - Sources gain `slug` and the full block `text`.
  - The chat stream stops cleanly when the client disconnects.
  - A new route serves the three PDFs.

**Tech Stack:** Next.js 16.4 App Router, React 19.3 client components, Tailwind CSS 4 (already set up), vitest 4 (node environment), TypeScript. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-14-rag-chatbot-design.md`. This plan implements §6.1 (UI), the §6 response contract, §7 ("Generation fails → error message in chat; sources still shown") and §10 row 7.

## Global Constraints

- Run every npm command from `web/`. The Docker DB must be up for `npm run test:int` and for live questions.
- The UI is `app/page.tsx` rendering `components/Chat.tsx`. The selector offers **GPO / GEP / iMac / All documents** (`scope` = `gpo|gep|imac|all`).
- Citation chips `[S#]` open the sources drawer, which shows the chunk text, section path, page, kind and an **"Open page"** link to `/api/pdf/<slug>#page=N`.
- "Show retrieval" renders the debug block: router output, ranks and rerank scores. Answers with verifier failures show a warning badge.
- Client code imports only **types** from `lib/rag/*`. `GEMINI_API_KEY` and `pg` must never reach the client bundle (spec §7). `next build` would fail if they did.
- The client sends `history` from completed question/answer pairs only, newest 6 turns. The question is at most 1000 characters.
- Model and PDF text is rendered as React text, never as HTML (`dangerouslySetInnerHTML` is not used anywhere).
- Commits: one per task, short imperative subject only (≤ 50 chars). **No `Co-Authored-By` trailer, no body.**

## Review Focus

1. **Stream arrives in odd pieces.** Lines can be split across network chunks, several can share one chunk, a multi-byte character can be split, and the final line may lack a newline. The answer must render intact. Pinned in Task 3.
2. **Failures before or during streaming.** HTTP 400/503, a network failure, or a malformed or truncated stream must end in one readable error in the thread. Sources and partial text are kept, and sending works again. Pinned in Tasks 3 and 5, and in the browser check in Task 6.
3. **Stopping or leaving mid-answer.** The answer shows "Stopped.", and that turn is not sent as history. The server stops spending Gemini calls and never writes to the closed stream. Pinned in Tasks 1 and 3, and in the browser check in Task 6.
4. **Odd model output.** Citations to sources that don't exist, `[S1, S2]` lists, Markdown bullets and bold, and text that looks like HTML must render as plain text, with chips only for real sources. Pinned in Tasks 4 and 5.
5. **PDF route abuse.** Unknown slugs, `../.env` and prototype keys (`constructor`, `__proto__`) get 404, and the route never reads outside the three fixed files. Pinned in Task 2.

---

### Task 1: Sources for the UI and stop on disconnect

**Files:**
- Modify: `web/lib/rag/answer.ts` (the `SourceInfo` type and `sourceInfo()`), `web/lib/rag/answer.test.ts`, `web/app/api/chat/route.ts`
- Create: `web/lib/rag/ndjson.ts`
- Test: `web/lib/rag/ndjson.test.ts`

**Interfaces:**
- Consumes: `ChatEvent`, `answer` (sub-project 6); `errorMessage` from `@/lib/rag/health` (sub-project 1).
- Produces:
  - `SourceInfo = { id; slug; doc; sectionPath; pageStart; pageEnd; kind; snippet; text }`
  - `ndjsonStream(run: (emit) => Promise<void>): ReadableStream<Uint8Array>`. After the client cancels, `emit` throws, so the answer stops.

- [ ] **Step 1: Write the failing tests.** In `web/lib/rag/answer.test.ts`, replace

```ts
    expect(sources.sources.map((s) => s.id)).toEqual([1]);
```

with

```ts
    expect(sources.sources.map((s) => s.id)).toEqual([1]);
    expect(sources.sources[0]).toMatchObject({ slug: "gep", doc: "Global Economic Prospects", pageStart: 96, text: expect.stringContaining("6.3 percent") });
```

Create `web/lib/rag/ndjson.test.ts`:

```ts
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/rag/answer.test.ts lib/rag/ndjson.test.ts`
Expected: FAIL. `answer.test.ts` reports `slug` and `text` missing from the source, and `ndjson.test.ts` fails with `Cannot find package '@/lib/rag/ndjson'`.

- [ ] **Step 3: Add `slug` and `text` to sources.** In `web/lib/rag/answer.ts`, replace

```ts
export type SourceInfo = { id: number; doc: string; sectionPath: string; pageStart: number; pageEnd: number; kind: string; snippet: string };
```

with

```ts
export type SourceInfo = {
  id: number; slug: string; doc: string; sectionPath: string; pageStart: number; pageEnd: number; kind: string; snippet: string;
  text: string; // the whole block the model saw, for the sources drawer
};
```

and replace

```ts
    id: block.id, doc: docTitle, sectionPath: block.sectionPath, pageStart: block.pageStart, pageEnd: block.pageEnd,
    kind: block.kind, snippet: block.text.slice(0, 300),
```

with

```ts
    id: block.id, slug: block.docSlug, doc: docTitle, sectionPath: block.sectionPath, pageStart: block.pageStart,
    pageEnd: block.pageEnd, kind: block.kind, snippet: block.text.slice(0, 300), text: block.text,
```

- [ ] **Step 4: Write `web/lib/rag/ndjson.ts`**

```ts
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
```

- [ ] **Step 5: Use it in the route.** Replace the whole of `web/app/api/chat/route.ts` with:

```ts
import { NextResponse } from "next/server";
import { getEnv } from "@/lib/env";
import { answer } from "@/lib/rag/answer";
import { getPool } from "@/lib/rag/db";
import { productionDeps } from "@/lib/rag/deps";
import { errorMessage } from "@/lib/rag/health";
import { ndjsonStream } from "@/lib/rag/ndjson";
import { parseChatRequest } from "@/lib/rag/request";
import { evaluateSchema, readSchemaFacts } from "@/lib/rag/schema";

let schemaCheck: Promise<string[]> | undefined; // spec §7: refuse to answer if embeddings don't match the DB

function schemaProblems(): Promise<string[]> {
  const env = getEnv();
  schemaCheck ??= readSchemaFacts(getPool())
    .then((facts) => evaluateSchema(facts, { embedModel: env.GEMINI_EMBED_MODEL, embedDim: env.EMBED_DIM }))
    .catch((error) => {
      schemaCheck = undefined; // retry on the next request once the DB is back
      return [`Database unavailable: ${errorMessage(error)}`];
    });
  return schemaCheck;
}

export async function POST(req: Request) {
  const parsed = parseChatRequest(await req.json().catch(() => null));
  if (!parsed.ok) return NextResponse.json({ error: parsed.error }, { status: 400 });
  const problems = await schemaProblems();
  if (problems.length) return NextResponse.json({ error: problems.join("; ") }, { status: 503 });
  const stream = ndjsonStream((emit) => answer(parsed.request, productionDeps(), emit));
  return new Response(stream, { headers: { "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store" } });
}
```

- [ ] **Step 6: Run the tests and the type check**

Run: `cd web && npm test && npx next typegen && npx tsc --noEmit`
Expected: 65 passed, 5 skipped; no type errors

- [ ] **Step 7: Commit**

```bash
git add web/lib/rag/answer.ts web/lib/rag/answer.test.ts web/lib/rag/ndjson.ts web/lib/rag/ndjson.test.ts web/app/api/chat/route.ts
git commit -m "Send source text and stop on disconnect"
```

---

### Task 2: PDF route

**Files:**
- Create: `web/lib/pdf.ts`, `web/app/api/pdf/[slug]/route.ts`
- Test: `web/lib/pdf.test.ts`

**Interfaces:**
- Produces:
  - `PDF_FILES` (slug → file name), `pdfPath(slug): string | null`
  - `GET /api/pdf/[slug]`: 200 `application/pdf`, served inline; 404 JSON for an unknown slug or a missing file

- [ ] **Step 1: Write the failing test.** Create `web/lib/pdf.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { GET } from "@/app/api/pdf/[slug]/route";
import { pdfPath } from "@/lib/pdf";

const get = (slug: string) => GET(new Request(`http://test/api/pdf/${slug}`), { params: Promise.resolve({ slug }) });

describe("PDF route", () => {
  it("serves a known document inline as a PDF", async () => {
    const res = await get("imac");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("content-disposition")).toBe('inline; filename="imac.pdf"');
    expect(new TextDecoder().decode((await res.arrayBuffer()).slice(0, 5))).toBe("%PDF-");
  });

  it("returns 404 for unknown slugs and never resolves paths from user input", async () => {
    for (const slug of ["nope", "../.env", "constructor", "__proto__"]) {
      expect(pdfPath(slug)).toBeNull();
      expect((await get(slug)).status).toBe(404);
    }
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- lib/pdf.test.ts`
Expected: FAIL with `Cannot find package '@/app/api/pdf/[slug]/route'`

- [ ] **Step 3: Write `web/lib/pdf.ts`**

```ts
import path from "node:path";

// The three fixed source PDFs (spec §1), served for "Open page" links. Only these slugs map to files.
export const PDF_FILES: Record<string, string> = {
  gpo: "GPO-Anual-report.pdf",
  gep: "GEP-Jun-2026.pdf",
  imac: "iMac_PER_Oct2024.pdf",
};

/** Absolute path of a document's PDF (repo-root docs/, one level above web/), or null for an unknown slug. */
export function pdfPath(slug: string): string | null {
  const file = Object.hasOwn(PDF_FILES, slug) ? PDF_FILES[slug] : undefined;
  return file ? path.resolve(process.cwd(), "..", "docs", file) : null;
}
```

- [ ] **Step 4: Write `web/app/api/pdf/[slug]/route.ts`**

```ts
import { readFile } from "node:fs/promises";
import { NextResponse } from "next/server";
import { pdfPath } from "@/lib/pdf";

export async function GET(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const file = pdfPath(slug);
  if (!file) return NextResponse.json({ error: `unknown document: ${slug}` }, { status: 404 });
  const body = await readFile(file).catch(() => null);
  if (!body) return NextResponse.json({ error: `PDF missing for ${slug}` }, { status: 404 });
  return new Response(new Uint8Array(body), {
    headers: { "Content-Type": "application/pdf", "Content-Disposition": `inline; filename="${slug}.pdf"`, "Cache-Control": "public, max-age=3600" },
  });
}
```

- [ ] **Step 5: Run the tests**

Run: `cd web && npm test`
Expected: 67 passed, 5 skipped

- [ ] **Step 6: Commit**

```bash
git add web/lib/pdf.ts web/lib/pdf.test.ts "web/app/api/pdf/[slug]/route.ts"
git commit -m "Serve source PDFs"
```

---

### Task 3: Client stream and thread state

**Files:**
- Create: `web/lib/chat/stream.ts`, `web/lib/chat/thread.ts`
- Test: `web/lib/chat/stream.test.ts`, `web/lib/chat/thread.test.ts`

**Interfaces:**
- Consumes: the types `ChatEvent`, `ChatRequest`, `Debug`, `SourceInfo` and `Turn` from `@/lib/rag/answer`, and `Scope` from `@/lib/rag/router`.
- Produces:
  - `readEvents(body): AsyncGenerator<ChatEvent>`
  - `askChat(request, onEvent, { signal?, fetchFn? }): Promise<void>`. Every failure ends with one `error` event; a stop ends quietly.
  - `UserMessage`, `AssistantMessage` (`status: "streaming" | "done" | "error" | "stopped"`), `Message`
  - `emptyAnswer()`, `applyEvent(message, event)`, `historyFor(messages): Turn[]`, `HISTORY_TURNS = 6`

- [ ] **Step 1: Write the failing tests.** Create `web/lib/chat/stream.test.ts`:

```ts
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
```

Create `web/lib/chat/thread.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { applyEvent, emptyAnswer, historyFor, type Message } from "@/lib/chat/thread";
import type { SourceInfo } from "@/lib/rag/answer";

const source: SourceInfo = {
  id: 1, slug: "gep", doc: "GEP", sectionPath: "Ch. 2", pageStart: 96, pageEnd: 97, kind: "text", snippet: "s", text: "t",
};

describe("applyEvent", () => {
  it("builds an answer from sources, text deltas and done", () => {
    let m = emptyAnswer();
    m = applyEvent(m, { type: "sources", sources: [source] });
    m = applyEvent(m, { type: "text", delta: "Growth " });
    m = applyEvent(m, { type: "text", delta: "is 6.3 percent [S1]." });
    m = applyEvent(m, { type: "done", warnings: ["unverified figures: 7"], debug: { question: "q", route: { docs: ["gep"], paths: [] }, candidates: [] } });
    expect(m).toMatchObject({ status: "done", text: "Growth is 6.3 percent [S1].", sources: [source], warnings: ["unverified figures: 7"] });
  });

  it("keeps the sources and partial text when an error arrives", () => {
    const m = applyEvent(applyEvent(applyEvent(emptyAnswer(), { type: "sources", sources: [source] }), { type: "text", delta: "Part" }), {
      type: "error", message: "answer generation failed: 429",
    });
    expect(m).toMatchObject({ status: "error", text: "Part", sources: [source], error: "answer generation failed: 429" });
  });
});

describe("historyFor", () => {
  const done = (text: string): Message => ({ ...emptyAnswer(), status: "done", text });
  const ask = (text: string): Message => ({ role: "user", text, scope: "all" });

  it("sends completed pairs only, newest 6 turns", () => {
    const messages: Message[] = [
      ask("q1"), done("a1"), ask("q2"), done("a2"), ask("q3"), { ...emptyAnswer(), status: "error", error: "x" },
      ask("q4"), { ...emptyAnswer(), status: "stopped", text: "half" }, ask("q5"), done("a5"), ask("q6"), done("a6"),
    ];
    expect(historyFor(messages).map((t) => t.content)).toEqual(["q2", "a2", "q5", "a5", "q6", "a6"]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npm test -- lib/chat`
Expected: FAIL with `Cannot find package '@/lib/chat/stream'` and `'@/lib/chat/thread'`

- [ ] **Step 3: Write `web/lib/chat/stream.ts`**

```ts
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
```

- [ ] **Step 4: Write `web/lib/chat/thread.ts`**

```ts
import type { ChatEvent, Debug, SourceInfo, Turn } from "@/lib/rag/answer";
import type { Scope } from "@/lib/rag/router";

export type UserMessage = { role: "user"; text: string; scope: Scope };
export type AssistantMessage = {
  role: "assistant";
  status: "streaming" | "done" | "error" | "stopped";
  text: string;
  sources: SourceInfo[];
  warnings: string[];
  debug?: Debug;
  error?: string;
};
export type Message = UserMessage | AssistantMessage;

export const HISTORY_TURNS = 6; // the API keeps the last 6 (spec §6)

export function emptyAnswer(): AssistantMessage {
  return { role: "assistant", status: "streaming", text: "", sources: [], warnings: [] };
}

export function applyEvent(message: AssistantMessage, event: ChatEvent): AssistantMessage {
  switch (event.type) {
    case "sources":
      return { ...message, sources: event.sources };
    case "text":
      return { ...message, text: message.text + event.delta };
    case "done":
      return { ...message, status: "done", warnings: event.warnings, debug: event.debug };
    case "error":
      return { ...message, status: "error", error: event.message };
  }
}

/** Completed question/answer pairs only (a failed or stopped answer drops its question too), last 6 turns. */
export function historyFor(messages: Message[]): Turn[] {
  const turns: Turn[] = [];
  messages.forEach((message, i) => {
    const next = messages[i + 1];
    if (message.role === "user" && next?.role === "assistant" && next.status === "done" && next.text.trim()) {
      turns.push({ role: "user", content: message.text }, { role: "assistant", content: next.text });
    }
  });
  return turns.slice(-HISTORY_TURNS);
}
```

- [ ] **Step 5: Run the tests**

Run: `cd web && npm test`
Expected: 74 passed, 5 skipped

- [ ] **Step 6: Commit**

```bash
git add web/lib/chat/stream.ts web/lib/chat/stream.test.ts web/lib/chat/thread.ts web/lib/chat/thread.test.ts
git commit -m "Add client chat stream and thread state"
```

---

### Task 4: Answer formatting

**Files:**
- Create: `web/lib/chat/format.ts`
- Test: `web/lib/chat/format.test.ts`

**Interfaces:**
- Consumes: the `SourceInfo` type (Task 1).
- Produces:
  - `Inline`, `Block`
  - `inlineParts(text, known: Set<number>): Inline[]`, `parseAnswer(text, known): Block[]`
  - `pageLabel(source)` (`p.96` / `pp.95–97`), `pdfHref(source)` (`/api/pdf/<slug>#page=N`), `sourceTitle(source)` (`Doc › path`, or the document alone)

- [ ] **Step 1: Write the failing test.** Create `web/lib/chat/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { inlineParts, pageLabel, parseAnswer, pdfHref } from "@/lib/chat/format";

const known = new Set([1, 2, 3]);

describe("inlineParts", () => {
  it("turns citations into chips, including [S1][S3] and [S1, S2]", () => {
    expect(inlineParts("Growth is 6.3 percent [S1][S3]. Debt rose [S1, S2].", known)).toEqual([
      { type: "text", text: "Growth is 6.3 percent ", strong: false },
      { type: "cite", id: 1 }, { type: "cite", id: 3 },
      { type: "text", text: ". Debt rose ", strong: false },
      { type: "cite", id: 1 }, { type: "cite", id: 2 },
      { type: "text", text: ".", strong: false },
    ]);
  });

  it("keeps unknown citations and other brackets as plain text, and marks bold runs", () => {
    expect(inlineParts("**Risks:** see [S9] and [note]", known)).toEqual([
      { type: "text", text: "Risks:", strong: true },
      { type: "text", text: " see [S9] and [note]", strong: false },
    ]);
  });
});

describe("parseAnswer", () => {
  it("splits paragraphs and bullet or numbered list items", () => {
    const blocks = parseAnswer("Growth softens [S1].\nIt recovers later.\n\n- **India:** 6.6 percent [S2]\n2. Debt [S3]", known);
    expect(blocks.map((b) => (b.type === "li" ? `li ${b.marker}` : "p"))).toEqual(["p", "li •", "li 2."]);
    expect(blocks[0].parts[0]).toEqual({ type: "text", text: "Growth softens ", strong: false });
    expect(blocks[0].parts.at(-1)).toEqual({ type: "text", text: ". It recovers later.", strong: false });
  });
});

describe("source links", () => {
  it("labels pages and links to the PDF page", () => {
    expect(pageLabel({ pageStart: 96, pageEnd: 96 })).toBe("p.96");
    expect(pageLabel({ pageStart: 95, pageEnd: 97 })).toBe("pp.95–97");
    expect(pdfHref({ slug: "imac", pageStart: 7 })).toBe("/api/pdf/imac#page=7");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- lib/chat/format.test.ts`
Expected: FAIL with `Cannot find package '@/lib/chat/format'`

- [ ] **Step 3: Write `web/lib/chat/format.ts`**

```ts
import type { SourceInfo } from "@/lib/rag/answer";

// The answer model writes light Markdown (paragraphs, "- " / "1. " lists, **bold**) with [S#] citations.
// Rendered as plain React text: no HTML from the model or the PDFs is ever injected.

export type Inline = { type: "text"; text: string; strong: boolean } | { type: "cite"; id: number };
export type Block = { type: "p"; parts: Inline[] } | { type: "li"; marker: string; parts: Inline[] };

const CITATION = /\[(S\d+(?:\s*[,;]\s*S?\d+)*)\]/g; // [S1], also [S1, S2] and [S1; 3]
const LIST_ITEM = /^\s*([-*•]|\d+[.)])\s+(.*)$/;

/** Split text into runs and citation chips; a citation to a source that doesn't exist stays plain text. */
export function inlineParts(text: string, known: Set<number>): Inline[] {
  const parts: Inline[] = [];
  text.split(/\*\*(.+?)\*\*/g).forEach((segment, i) => {
    const strong = i % 2 === 1;
    let last = 0;
    for (const match of segment.matchAll(CITATION)) {
      const ids = match[1].split(/[,;]/).map((id) => Number(id.trim().replace(/^S/, "")));
      if (!ids.every((id) => known.has(id))) continue;
      if (match.index > last) parts.push({ type: "text", text: segment.slice(last, match.index), strong });
      ids.forEach((id) => parts.push({ type: "cite", id }));
      last = match.index + match[0].length;
    }
    if (last < segment.length) parts.push({ type: "text", text: segment.slice(last), strong });
  });
  return parts;
}

export function parseAnswer(text: string, known: Set<number>): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ type: "p", parts: inlineParts(paragraph.join(" "), known) });
    paragraph = [];
  };
  for (const line of text.split("\n")) {
    const item = line.match(LIST_ITEM);
    if (item) {
      flush();
      blocks.push({ type: "li", marker: /\d/.test(item[1]) ? item[1] : "•", parts: inlineParts(item[2], known) });
    } else if (line.trim()) {
      paragraph.push(line.trim());
    } else {
      flush();
    }
  }
  flush();
  return blocks;
}

export function pageLabel(source: Pick<SourceInfo, "pageStart" | "pageEnd">): string {
  return source.pageStart === source.pageEnd ? `p.${source.pageStart}` : `pp.${source.pageStart}–${source.pageEnd}`;
}

/** The source PDF opened at the passage's first page (browser PDF viewers honour #page=N). */
export function pdfHref(source: Pick<SourceInfo, "slug" | "pageStart">): string {
  return `/api/pdf/${encodeURIComponent(source.slug)}#page=${source.pageStart}`;
}

/** "GEP › Ch. 2 Regional Outlooks › South Asia", or the document alone for text before the first heading. */
export function sourceTitle(source: Pick<SourceInfo, "doc" | "sectionPath">): string {
  return source.sectionPath ? `${source.doc} › ${source.sectionPath}` : source.doc;
}
```

- [ ] **Step 4: Run the tests**

Run: `cd web && npm test`
Expected: 78 passed, 5 skipped

- [ ] **Step 5: Commit**

```bash
git add web/lib/chat/format.ts web/lib/chat/format.test.ts
git commit -m "Format answers with citation chips"
```

---

### Task 5: Answer, sources and retrieval components

**Files:**
- Modify: `web/vitest.config.ts` (also collect `*.test.tsx`)
- Create: `web/components/AnswerText.tsx`, `web/components/AssistantBubble.tsx`, `web/components/SourcesDrawer.tsx`, `web/components/DebugPanel.tsx`
- Test: `web/components/components.test.tsx`

**Interfaces:**
- Consumes: Tasks 3–4.
- Produces:
  - `<AnswerText text sourceIds onCite />`
  - `<AssistantBubble message showDebug onCite />` (progress text, "Stopped.", a `role="alert"` error, the `⚠` warning badge, source buttons and the debug panel)
  - `<SourcesDrawer sources active onClose />` (`<aside aria-label="Sources">`)
  - `<DebugPanel debug />`

- [ ] **Step 1: Collect `.tsx` tests.** In `web/vitest.config.ts`, replace

```ts
    include: ["**/*.test.ts"],
```

with

```ts
    include: ["**/*.test.ts", "**/*.test.tsx"],
```

- [ ] **Step 2: Write the failing tests.** Create `web/components/components.test.tsx`:

```tsx
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AssistantBubble } from "@/components/AssistantBubble";
import { SourcesDrawer } from "@/components/SourcesDrawer";
import { emptyAnswer, type AssistantMessage } from "@/lib/chat/thread";
import type { SourceInfo } from "@/lib/rag/answer";

const source: SourceInfo = {
  id: 1, slug: "gep", doc: "GEP", sectionPath: "Ch. 2 Regional Outlooks › South Asia", pageStart: 96, pageEnd: 97, kind: "text",
  snippet: "Growth", text: "Growth in SAR is expected to soften to 6.3 percent.",
};
const noop = () => {};
const bubble = (message: Partial<AssistantMessage>, showDebug = false) =>
  renderToStaticMarkup(<AssistantBubble message={{ ...emptyAnswer(), ...message }} showDebug={showDebug} onCite={noop} />);

describe("AssistantBubble", () => {
  it("renders citation chips for known sources, plain text for unknown ones, and the source list", () => {
    const html = bubble({ status: "done", text: "Growth softens to 6.3 percent [S1], not [S9].", sources: [source] });
    expect(html).toContain(">S1</button>");
    expect(html).toContain("not [S9].");
    expect(html).toContain("GEP › Ch. 2 Regional Outlooks › South Asia · pp.96–97");
  });

  it("shows a warning badge, errors, stopped answers and progress", () => {
    expect(bubble({ status: "done", text: "6.4 percent [S1]", sources: [source], warnings: ["unverified figures: 6.4"] })).toContain("⚠ Unverified figures: 6.4");
    expect(bubble({ status: "error", error: "answer generation failed: 429" })).toContain('role="alert"');
    expect(bubble({ status: "stopped", text: "Half" })).toContain("Stopped.");
    expect(bubble({})).toContain("Searching the documents…");
  });

  it("shows retrieval details only when asked", () => {
    const debug = { question: "q", route: { docs: ["gep"], paths: ["Ch. 2 Regional Outlooks › South Asia"] }, candidates: [{ chunkId: 7924, vecRank: 1, kwRank: 3, rrf: 0.0323, rerank: 3 }] };
    expect(bubble({ status: "done", text: "x", debug })).not.toContain("Retrieval");
    const html = bubble({ status: "done", text: "x", debug }, true);
    expect(html).toContain("Section filter: Ch. 2 Regional Outlooks › South Asia");
    expect(html).toContain("<td>7924</td><td>1</td><td>3</td><td>0.0323</td><td>3</td>");
  });
});

describe("SourcesDrawer", () => {
  it("lists the clicked source first with its text and a link to its PDF page", () => {
    const other = { ...source, id: 2, slug: "imac", doc: "iMac", sectionPath: "", pageStart: 7, pageEnd: 7, text: "Stand: 100 percent recycled aluminum." };
    const html = renderToStaticMarkup(<SourcesDrawer sources={[source, other]} active={2} onClose={noop} />);
    expect(html.indexOf("S2 · iMac")).toBeLessThan(html.indexOf("S1 · GEP"));
    expect(html).toContain('href="/api/pdf/imac#page=7" target="_blank"');
    expect(html).toContain("Stand: 100 percent recycled aluminum.");
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd web && npm test -- components`
Expected: FAIL with `Cannot find package '@/components/AssistantBubble'`

- [ ] **Step 4: Write `web/components/AnswerText.tsx`**

```tsx
import { parseAnswer, type Inline } from "@/lib/chat/format";

type Props = { text: string; sourceIds: number[]; onCite: (id: number) => void };

function Parts({ parts, onCite }: { parts: Inline[]; onCite: (id: number) => void }) {
  return parts.map((part, i) =>
    part.type === "cite" ? (
      <button
        key={i}
        type="button"
        onClick={() => onCite(part.id)}
        className="mx-0.5 rounded bg-blue-100 px-1 align-baseline text-xs font-semibold text-blue-800 hover:bg-blue-200 dark:bg-blue-950 dark:text-blue-200"
      >
        S{part.id}
      </button>
    ) : part.strong ? (
      <strong key={i}>{part.text}</strong>
    ) : (
      <span key={i}>{part.text}</span>
    ),
  );
}

/** The answer's paragraphs and list items, with [S#] citations as clickable chips. */
export function AnswerText({ text, sourceIds, onCite }: Props) {
  return (
    <div className="space-y-2 leading-relaxed">
      {parseAnswer(text, new Set(sourceIds)).map((block, i) =>
        block.type === "li" ? (
          <div key={i} className="flex gap-2 pl-2">
            <span className="shrink-0 text-zinc-500">{block.marker}</span>
            <span><Parts parts={block.parts} onCite={onCite} /></span>
          </div>
        ) : (
          <p key={i}><Parts parts={block.parts} onCite={onCite} /></p>
        ),
      )}
    </div>
  );
}
```

- [ ] **Step 5: Write `web/components/DebugPanel.tsx`**

```tsx
import type { Debug } from "@/lib/rag/answer";

/** "Show retrieval": router output and each candidate's vector rank, keyword rank, RRF score and rerank score. */
export function DebugPanel({ debug }: { debug: Debug }) {
  return (
    <details open className="mt-3 rounded border border-zinc-200 p-2 text-xs dark:border-zinc-800">
      <summary className="cursor-pointer font-semibold">Retrieval</summary>
      <p className="mt-1">Searched as: {debug.question}</p>
      <p>Documents: {debug.route.docs.join(", ") || "none"}</p>
      <p>Section filter: {debug.route.paths.length ? debug.route.paths.join("; ") : "none"}</p>
      <table className="mt-2 w-full text-left tabular-nums">
        <thead>
          <tr className="text-zinc-500"><th>chunk</th><th>vector</th><th>keyword</th><th>RRF</th><th>rerank</th></tr>
        </thead>
        <tbody>
          {debug.candidates.map((c) => (
            <tr key={c.chunkId} className={c.rerank !== null && c.rerank >= 2 ? "font-semibold" : "text-zinc-500"}>
              <td>{c.chunkId}</td><td>{c.vecRank ?? "–"}</td><td>{c.kwRank ?? "–"}</td><td>{c.rrf.toFixed(4)}</td><td>{c.rerank ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
```

- [ ] **Step 6: Write `web/components/AssistantBubble.tsx`**

```tsx
import { AnswerText } from "@/components/AnswerText";
import { DebugPanel } from "@/components/DebugPanel";
import { pageLabel, sourceTitle } from "@/lib/chat/format";
import type { AssistantMessage } from "@/lib/chat/thread";

type Props = { message: AssistantMessage; showDebug: boolean; onCite: (id: number) => void };

export function AssistantBubble({ message, showDebug, onCite }: Props) {
  const { status, text, sources, warnings, debug, error } = message;
  return (
    <div className="rounded-lg bg-zinc-100 p-3 dark:bg-zinc-900">
      {text ? (
        <AnswerText text={text} sourceIds={sources.map((s) => s.id)} onCite={onCite} />
      ) : status === "streaming" ? (
        <p className="text-zinc-500">{sources.length ? "Writing the answer…" : "Searching the documents…"}</p>
      ) : null}
      {status === "stopped" && <p className="mt-2 text-sm text-zinc-500">Stopped.</p>}
      {error && <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}
      {warnings.length > 0 && (
        <p className="mt-2 inline-block rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200" title={warnings.join("\n")}>
          ⚠ {warnings.map((w) => w[0].toUpperCase() + w.slice(1)).join("; ")}
        </p>
      )}
      {sources.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-1 text-xs">
          {sources.map((s) => (
            <li key={s.id}>
              <button type="button" onClick={() => onCite(s.id)} className="rounded border border-zinc-300 px-1.5 py-0.5 text-left hover:bg-white dark:border-zinc-700 dark:hover:bg-zinc-800">
                <b>S{s.id}</b> {sourceTitle(s)} · {pageLabel(s)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {showDebug && debug && <DebugPanel debug={debug} />}
    </div>
  );
}
```

- [ ] **Step 7: Write `web/components/SourcesDrawer.tsx`**

```tsx
import { pageLabel, pdfHref, sourceTitle } from "@/lib/chat/format";
import type { SourceInfo } from "@/lib/rag/answer";

type Props = { sources: SourceInfo[]; active: number; onClose: () => void };

/** Every source of one answer, the clicked one first and highlighted: text, section, page, kind and a PDF link. */
export function SourcesDrawer({ sources, active, onClose }: Props) {
  const ordered = [...sources].sort((a, b) => Number(b.id === active) - Number(a.id === active));
  return (
    <aside aria-label="Sources" className="fixed inset-y-0 right-0 z-10 flex w-full max-w-md flex-col border-l border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-200 p-3 dark:border-zinc-800">
        <h2 className="font-semibold">Sources</h2>
        <button type="button" onClick={onClose} className="rounded px-2 hover:bg-zinc-100 dark:hover:bg-zinc-800" aria-label="Close sources">✕</button>
      </div>
      <ol className="flex-1 space-y-3 overflow-y-auto p-3">
        {ordered.map((s) => (
          <li key={s.id} className={`rounded border p-2 text-sm ${s.id === active ? "border-blue-500" : "border-zinc-200 dark:border-zinc-800"}`}>
            <p className="font-semibold">S{s.id} · {sourceTitle(s)}</p>
            <p className="text-xs text-zinc-500">
              {pageLabel(s)} · {s.kind} ·{" "}
              <a href={pdfHref(s)} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline dark:text-blue-300">Open page</a>
            </p>
            <p className="mt-2 whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">{s.text}</p>
          </li>
        ))}
      </ol>
    </aside>
  );
}
```

- [ ] **Step 8: Run the tests, type check and lint**

Run: `cd web && npm test && npx tsc --noEmit && npx eslint components lib/chat`
Expected: 82 passed, 5 skipped; no type errors; no lint output

- [ ] **Step 9: Commit**

```bash
git add web/vitest.config.ts web/components/AnswerText.tsx web/components/AssistantBubble.tsx web/components/SourcesDrawer.tsx web/components/DebugPanel.tsx web/components/components.test.tsx
git commit -m "Add answer and sources components"
```

---

### Task 6: Chat page

**Files:**
- Create: `web/components/Chat.tsx`
- Modify: `web/app/page.tsx` (replace the Next.js starter page), `web/app/layout.tsx` (title), `web/components/components.test.tsx`, `README.md`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces:
  - `SCOPES`, `EXAMPLES` (the brief's 9 questions by document, 3 for "All documents"), `<Chat />`
  - `/` renders the chat

- [ ] **Step 1: Write the failing test.** Append to `web/components/components.test.tsx`:

```tsx
describe("Chat", () => {
  it("starts with the document selector, the retrieval toggle and the brief's questions", async () => {
    const { Chat, EXAMPLES } = await import("@/components/Chat");
    const html = renderToStaticMarkup(<Chat />);
    for (const label of ["All documents", "GPO 2018", "World Bank GEP", "iMac report", "Show retrieval"]) expect(html).toContain(label);
    expect(html).toContain('aria-checked="true"');
    EXAMPLES.all.forEach((q) => expect(html).toContain(q.replace(/'/g, "&#x27;")));
    expect(Object.values(EXAMPLES).flat()).toHaveLength(12);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npm test -- components`
Expected: FAIL. The `Chat` test cannot load `@/components/Chat`.

- [ ] **Step 3: Write `web/components/Chat.tsx`**

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { AssistantBubble } from "@/components/AssistantBubble";
import { SourcesDrawer } from "@/components/SourcesDrawer";
import { askChat } from "@/lib/chat/stream";
import { applyEvent, emptyAnswer, historyFor, type AssistantMessage, type Message } from "@/lib/chat/thread";
import type { Scope } from "@/lib/rag/router";

export const SCOPES: { value: Scope; label: string }[] = [
  { value: "all", label: "All documents" },
  { value: "gpo", label: "GPO 2018" },
  { value: "gep", label: "World Bank GEP" },
  { value: "imac", label: "iMac report" },
];

// The brief's questions (spec §8.1), offered as starters for the selected document.
export const EXAMPLES: Record<Scope, string[]> = {
  gpo: [
    "How many depository libraries did GPO staff visit in FY 2018?",
    "What system replaced FDsys, and when was it fully deployed?",
    "How much did GPO procure from private-sector printers in FY 2018?",
  ],
  gep: [
    "What is the growth outlook for South Asia?",
    "What does Chapter 3 say about sovereign debt?",
    "What are the main risks according to the Executive Summary?",
  ],
  imac: [
    "How much less energy does the iMac use than the ENERGY STAR requirement?",
    "What is the iMac's total carbon footprint, and how is it split between production and use?",
    "What is the iMac stand made of?",
  ],
  all: [
    "How many depository libraries did GPO staff visit in FY 2018?",
    "What is the growth outlook for South Asia?",
    "What is the iMac stand made of?",
  ],
};

const MAX_QUESTION = 1000; // the API's limit

export function Chat() {
  const [scope, setScope] = useState<Scope>("all");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [showDebug, setShowDebug] = useState(false);
  const [drawer, setDrawer] = useState<{ message: number; source: number } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const last = messages.at(-1);
  const busy = last?.role === "assistant" && last.status === "streaming";

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" }); // braces: newer browsers return a Promise, which an effect must not
  }, [messages.length]);

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    const index = messages.length + 1; // the answer's position once both messages are added
    const update = (change: (answer: AssistantMessage) => AssistantMessage) =>
      setMessages((all) => all.map((m, i) => (i === index && m.role === "assistant" ? change(m) : m)));
    const controller = new AbortController();
    abort.current = controller;
    setMessages((all) => [...all, { role: "user", text: question, scope }, emptyAnswer()]);
    setInput("");
    await askChat({ question, scope, history: historyFor(messages) }, (event) => update((a) => applyEvent(a, event)), {
      signal: controller.signal,
    });
    if (controller.signal.aborted) update((a) => (a.status === "streaming" ? { ...a, status: "stopped" } : a));
    abort.current = null;
  }

  const drawerMessage = drawer ? messages[drawer.message] : undefined;

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-4">
      <header className="sticky top-0 z-0 flex flex-wrap items-center gap-3 border-b border-zinc-200 bg-background py-3 dark:border-zinc-800">
        <h1 className="mr-auto text-lg font-semibold">Document Q&amp;A</h1>
        <div role="radiogroup" aria-label="Document" className="flex flex-wrap gap-1 text-sm">
          {SCOPES.map((s) => (
            <button
              key={s.value}
              type="button"
              role="radio"
              aria-checked={scope === s.value}
              onClick={() => setScope(s.value)}
              className={`rounded-full border px-3 py-1 ${scope === s.value ? "border-blue-600 bg-blue-600 text-white" : "border-zinc-300 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"}`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-sm">
          <input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} />
          Show retrieval
        </label>
      </header>

      <main className="flex-1 space-y-4 py-4">
        {messages.length === 0 && (
          <div className="space-y-2 text-sm">
            <p className="text-zinc-500">Ask about the GPO 2018 Annual Report, the World Bank&apos;s Global Economic Prospects (June 2026) or the iMac Product Environmental Report. Try:</p>
            {EXAMPLES[scope].map((q) => (
              <button key={q} type="button" onClick={() => send(q)} className="block rounded border border-zinc-300 px-3 py-2 text-left hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
                {q}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="ml-auto max-w-[85%] rounded-lg bg-blue-600 p-3 text-white">
              <p className="whitespace-pre-wrap">{m.text}</p>
              <p className="mt-1 text-xs opacity-75">{SCOPES.find((s) => s.value === m.scope)?.label}</p>
            </div>
          ) : (
            <AssistantBubble key={i} message={m} showDebug={showDebug} onCite={(source) => setDrawer({ message: i, source })} />
          ),
        )}
        <div ref={bottom} />
      </main>

      <form
        className="sticky bottom-0 flex gap-2 border-t border-zinc-200 bg-background py-3 dark:border-zinc-800"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send(input);
            }
          }}
          maxLength={MAX_QUESTION}
          rows={2}
          placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
          aria-label="Question"
          className="flex-1 resize-none rounded border border-zinc-300 bg-transparent p-2 dark:border-zinc-700"
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()} className="rounded bg-zinc-600 px-4 text-white">Stop</button>
        ) : (
          <button type="submit" disabled={!input.trim()} className="rounded bg-blue-600 px-4 text-white disabled:opacity-50">Send</button>
        )}
      </form>

      {drawer && drawerMessage?.role === "assistant" && (
        <SourcesDrawer sources={drawerMessage.sources} active={drawer.source} onClose={() => setDrawer(null)} />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Replace `web/app/page.tsx`**

```tsx
import { Chat } from "@/components/Chat";

export default function Home() {
  return <Chat />;
}
```

- [ ] **Step 5: Retitle the app.** In `web/app/layout.tsx`, replace

```tsx
  title: "Create Next App",
  description: "Generated by create next app",
```

with

```tsx
  title: "Document Q&A",
  description: "Grounded answers with citations over three reports",
```

- [ ] **Step 6: Run every check and the production build**

Run: `cd web && npm test && npm run test:int && npx next typegen && npx tsc --noEmit && npx eslint . && npx next build`
Expected:
- 83 passed, 5 skipped; then 87 passed, 1 skipped.
- No type errors.
- eslint shows only the existing `_omit` warning in `lib/env.test.ts`.
- The build lists `○ /`, `ƒ /api/chat`, `ƒ /api/health` and `ƒ /api/pdf/[slug]`.

- [ ] **Step 7: Browser check with no Gemini calls.** Run `cd web && npm run dev`. Open http://localhost:3100 and stub the API by pasting this into the browser console. It answers every question with one canned answer; reload the page to remove it.

```js
const realFetch = window.fetch;
window.fetch = async (url, init) => url !== "/api/chat" ? realFetch(url, init) : new Response([
  {type:"sources",sources:[{id:1,slug:"gep",doc:"Global Economic Prospects",sectionPath:"Ch. 2 Regional Outlooks › South Asia",pageStart:96,pageEnd:98,kind:"text",snippet:"",text:"Growth in SAR is projected to soften to 6.3 percent in 2026."}]},
  {type:"text",delta:"Growth is projected to soften to **6.3 percent** in 2026 [S1].\n\n- Other: 9 percent [S1]"},
  {type:"done",warnings:["unverified figures: 9"],debug:{question:"q",route:{docs:["gep"],paths:["Ch. 2 Regional Outlooks › South Asia"]},candidates:[{chunkId:1,vecRank:1,kwRank:2,rrf:0.032,rerank:3}]}},
].map((e) => JSON.stringify(e)).join("\n"));
```

Expected:
- The starter questions change with the selector.
- Clicking a starter shows the answer with bold text, a bullet, an `S1` chip and the badge `⚠ Unverified figures: 9`.
- `S1` opens the drawer, and "Open page" opens the GEP PDF at page 96 in a new tab.
- "Show retrieval" shows the section filter and the ranks table.
- The browser console shows no errors.

- [ ] **Step 8: Walkthrough with live answers.** This uses about 1 embedding and 2 generations per question; the free tier allows about 20 Flash answers a day. Reload the page to remove the stub. For each document, pick it in the selector and click its three starter questions.

Expected:
- **GPO:** 166 libraries; govinfo replaced FDsys with full deployment in FY 2018; more than $375 million.
- **GEP:** South Asia sources only; Chapter 3 sources only; Executive Summary sources only.
- **iMac:** 58 percent less energy; 346 kg with production 50% and use 45%; the stand is 100 percent recycled aluminum.
- Every answer has chips that open the right passage and PDF page.
- Also ask "What is the iMac battery life?" with "iMac report" selected: it is refused.
- Click Stop during an answer: "Stopped." appears and Send comes back.

Record anything wrong as a finding; don't patch it in this task. Stop the dev server.

- [ ] **Step 9: Document the UI.** Append to `README.md`:

````markdown

## Chat UI

```bash
cd web && npm run dev   # http://localhost:3100
```

Pick a document (or "All documents") and ask a question, or click one of the starter questions. `[S#]` chips open
the sources drawer: the passage the answer used, its section and page, and an "Open page" link to the PDF.
"Show retrieval" shows the router's filters and each candidate's vector, keyword and rerank scores; ⚠ marks figures
the verifier could not find in the cited sources.
````

- [ ] **Step 10: Commit**

```bash
git add web/components/Chat.tsx web/components/components.test.tsx web/app/page.tsx web/app/layout.tsx README.md
git commit -m "Add chat page"
```

---

## Sub-project acceptance (spec §10, row 7)

- [ ] `cd web && npm test` → 83 passed, 5 skipped; `npm run test:int` → 87 passed, 1 skipped
- [ ] `npx next typegen && npx tsc --noEmit` is clean; eslint shows only the existing `_omit` warning; `npx next build` succeeds
- [ ] Manual walkthrough of all 3 documents in the browser (Task 6, Steps 7–8)
- [ ] `git log --oneline` shows the plan and 6 short task commits, with no trailers

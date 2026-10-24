"use client";

import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { AssistantBubble } from "@/components/AssistantBubble";
import { DOCS, DocMark, docStyle } from "@/components/docs";
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
  const field = useRef<HTMLTextAreaElement>(null);
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

  function reset() {
    setMessages([]);
    setDrawer(null);
    field.current?.focus();
  }

  /** Arrow keys move the selection between documents, as in any radio group. */
  function onScopeKey(e: KeyboardEvent<HTMLButtonElement>) {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const next = (SCOPES.findIndex((s) => s.value === scope) + step + SCOPES.length) % SCOPES.length;
    setScope(SCOPES[next].value);
    (e.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
  }

  const drawerMessage = drawer ? messages[drawer.message] : undefined;

  const placeholder = scope === "all" ? "Ask about any of the three reports…" : `Ask about the ${DOCS[scope].title}…`;

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-4 sm:px-6">
      <header className="sticky top-0 z-10 border-b border-rule bg-paper/90 pt-5 pb-3 backdrop-blur">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg leading-tight font-bold tracking-tight">Document Q&amp;A</h1>
            <p className="text-sm text-muted">Cited answers from three reports</p>
          </div>
          <div className="flex items-center gap-4 pt-0.5 text-sm">
            {messages.length > 0 && (
              <button type="button" onClick={reset} disabled={busy} className="font-medium text-muted hover:text-ink disabled:opacity-40">
                New conversation
              </button>
            )}
            <label className="flex cursor-pointer items-center gap-1.5 text-muted hover:text-ink">
              <input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} className="accent-(--ink)" />
              Show retrieval
            </label>
          </div>
        </div>
        <div role="radiogroup" aria-label="Document" className="mt-3 flex flex-wrap gap-1">
          {SCOPES.map((s) => (
            <button
              key={s.value}
              type="button"
              role="radio"
              aria-checked={scope === s.value}
              tabIndex={scope === s.value ? 0 : -1}
              onClick={() => setScope(s.value)}
              onKeyDown={onScopeKey}
              style={docStyle(s.value)}
              className={`flex shrink-0 items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${scope === s.value ? "border-(--doc) bg-surface font-semibold shadow-sm" : "border-transparent text-muted hover:bg-surface hover:text-ink"}`}
            >
              <DocMark scope={s.value} />
              {s.label}
            </button>
          ))}
        </div>
      </header>

      <main className="flex flex-1 flex-col py-8">
        {messages.length === 0 && (
          <div className="my-auto py-6">
            <h2 className="font-serif text-[2rem] leading-tight">
              {scope === "all" ? "Ask anything across the three reports" : `Ask the ${DOCS[scope].title}`}
            </h2>
            <p className="mt-2 max-w-prose text-muted">
              Every answer cites the passages it draws on. Click a citation to read the passage and open the page in the PDF.
            </p>
            <div className={`mt-8 grid gap-6 ${scope === "all" ? "sm:grid-cols-3" : ""}`}>
              {(scope === "all" ? (Object.keys(DOCS) as (keyof typeof DOCS)[]) : [scope]).map((doc) => (
                <section key={doc} style={docStyle(doc)} className="flex flex-col border-l-4 border-(--doc) pl-4">
                  <h2 className="font-semibold">{DOCS[doc].title}</h2>
                  <p className="text-sm text-muted">{DOCS[doc].about}</p>
                  <ul className="mt-2 space-y-1 sm:mt-auto sm:pt-2">
                    {EXAMPLES[scope].filter((q) => EXAMPLES[doc].includes(q)).map((q) => (
                      <li key={q}>
                        <button
                          type="button"
                          onClick={() => send(q)}
                          className="rounded-md px-2 py-1 -ml-2 text-left font-serif text-[1.05rem] text-(--doc) hover:bg-(--doc)/8"
                        >
                          {q}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className={i > 0 ? "mt-8 border-t border-rule pt-8" : ""}>
              <p className="flex items-center gap-1.5 text-xs text-muted">
                <DocMark scope={m.scope} />
                {SCOPES.find((s) => s.value === m.scope)?.label}
              </p>
              <p className="mt-1.5 whitespace-pre-wrap text-lg leading-snug font-semibold">{m.text}</p>
            </div>
          ) : (
            <div key={i} className="mt-4">
              <AssistantBubble message={m} showDebug={showDebug} onCite={(source) => setDrawer({ message: i, source })} />
            </div>
          ),
        )}
        <div ref={bottom} />
      </main>

      <form
        className="sticky bottom-0 bg-paper pt-2 pb-4"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <div style={docStyle(scope)} className="rounded-xl border border-rule bg-surface shadow-sm focus-within:border-(--doc)">
          <textarea
            ref={field}
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
            placeholder={placeholder}
            aria-label="Question"
            className="field-sizing-content block max-h-48 min-h-[3.25rem] w-full resize-none bg-transparent px-4 pt-3 placeholder:text-muted focus-visible:outline-none"
          />
          <div className="flex items-center justify-between gap-3 px-3 pb-2.5 pl-4">
            <p className="text-xs text-muted">
              {input.length > MAX_QUESTION * 0.8 ? (
                <span className={input.length >= MAX_QUESTION ? "text-error-ink" : ""}>{input.length} / {MAX_QUESTION} characters</span>
              ) : (
                <span className="hidden sm:inline">Enter to send, Shift+Enter for a new line</span>
              )}
            </p>
            {busy ? (
              <button
                type="button"
                onClick={(e) => e.detail < 2 && abort.current?.abort()} // a double-clicked Send lands its 2nd click here
                className="rounded-lg border border-rule px-4 py-1.5 text-sm font-semibold hover:bg-paper"
              >
                Stop
              </button>
            ) : (
              <button type="submit" disabled={!input.trim()} className="rounded-lg bg-(--doc) px-4 py-1.5 text-sm font-semibold text-surface disabled:opacity-40">
                Send
              </button>
            )}
          </div>
        </div>
      </form>

      {drawer && drawerMessage?.role === "assistant" && (
        <SourcesDrawer sources={drawerMessage.sources} active={drawer.source} onClose={() => setDrawer(null)} />
      )}
    </div>
  );
}

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
          <button
            type="button"
            onClick={(e) => e.detail < 2 && abort.current?.abort()} // a double-clicked Send lands its 2nd click here
            className="rounded bg-zinc-600 px-4 text-white"
          >
            Stop
          </button>
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

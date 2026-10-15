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

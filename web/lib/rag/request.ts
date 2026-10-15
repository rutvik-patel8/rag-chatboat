import { z } from "zod";
import type { ChatRequest } from "@/lib/rag/answer";

const ChatRequestSchema = z.object({
  question: z.string().trim().min(1).max(1000),
  scope: z.enum(["gpo", "gep", "imac", "all"]).default("all"),
  history: z.array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().max(4000) })).max(6).default([]),
});

export function parseChatRequest(body: unknown): { ok: true; request: ChatRequest } | { ok: false; error: string } {
  const result = ChatRequestSchema.safeParse(body);
  if (!result.success) {
    return { ok: false, error: result.error.issues.map((i) => `${i.path.join(".") || "body"}: ${i.message}`).join("; ") };
  }
  return { ok: true, request: result.data };
}

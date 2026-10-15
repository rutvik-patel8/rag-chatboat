import { z } from "zod";
import type { ChatRequest } from "@/lib/rag/answer";

const ChatRequestSchema = z.object({
  question: z.string().trim().min(1).max(1000),
  scope: z.enum(["gpo", "gep", "imac", "all"]).default("all"),
  history: z
    .array(z.object({ role: z.enum(["user", "assistant"]), content: z.string().max(20_000) }))
    .max(100)
    .default([])
    .transform((turns) => turns.slice(-6)), // spec §6.1: the last 6 turns
  options: z
    .strictObject({ vectorOnly: z.boolean(), noRerank: z.boolean(), noRouter: z.boolean(), noFootnotes: z.boolean() })
    .partial()
    .optional(),
});

export function parseChatRequest(body: unknown): { ok: true; request: ChatRequest } | { ok: false; error: string } {
  const result = ChatRequestSchema.safeParse(body);
  if (!result.success) {
    return { ok: false, error: result.error.issues.map((i) => `${i.path.join(".") || "body"}: ${i.message}`).join("; ") };
  }
  const { options, ...request } = result.data;
  // Ablation switches are for the evaluation (spec §6): never honoured in production.
  return { ok: true, request: process.env.NODE_ENV === "production" || !options ? request : { ...request, options } };
}

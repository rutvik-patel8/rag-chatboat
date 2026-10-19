import type { EvalQuestion } from "@/lib/eval/questions";
import type { SourceInfo } from "@/lib/rag/answer";
import { generateJson, type GenClient } from "@/lib/rag/genai";

// Gemini judge for the broad GEP questions (spec §8.2): rubric coverage and claims the sources don't support.

export type Judgement = { covered: number; total: number; unsupported: string[] };

const SCHEMA = {
  type: "object",
  properties: {
    covered: { type: "array", items: { type: "integer" } },
    unsupported: { type: "array", items: { type: "string" } },
  },
  required: ["covered", "unsupported"],
};

export function judgePrompt(q: EvalQuestion, answer: string, sources: SourceInfo[]): string {
  const rubric = (q.rubric ?? []).map((point, i) => `${i + 1}. ${point}`).join("\n");
  const context = sources.map((s) => `[S${s.id}] ${s.text}`).join("\n\n");
  return (
    "You grade an answer to a question about a report.\n" +
    "covered: the numbers of the rubric points the answer states (in any wording).\n" +
    "unsupported: claims in the answer that the sources do not support (empty if none). Quote each claim briefly.\n\n" +
    `Question: ${q.question}\n\nRubric:\n${rubric}\n\nAnswer:\n${answer}\n\nSources:\n${context}`
  );
}

export async function judge(client: GenClient, model: string, q: EvalQuestion, answer: string, sources: SourceInfo[]): Promise<Judgement> {
  const total = q.rubric?.length ?? 0;
  const result = await generateJson<{ covered: number[]; unsupported: string[] }>(client, model, judgePrompt(q, answer, sources), SCHEMA);
  const covered = new Set(result.covered.filter((n) => Number.isInteger(n) && n >= 1 && n <= total)).size;
  return { covered, total, unsupported: result.unsupported };
}

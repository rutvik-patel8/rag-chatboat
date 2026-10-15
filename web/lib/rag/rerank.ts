import { sectionLabel } from "@/lib/rag/context";
import type { Candidate } from "@/lib/rag/retrieve";
import { generateJson, type GenClient } from "@/lib/rag/genai";

export const KEEP = 8;
export const MIN_RELEVANCE = 2;
const SCHEMA = {
  type: "object",
  properties: {
    scores: {
      type: "array",
      items: { type: "object", properties: { id: { type: "integer" }, score: { type: "integer" } }, required: ["id", "score"] },
    },
  },
  required: ["scores"],
};

export function rerankPrompt(question: string, candidates: Candidate[]): string {
  const passages = candidates
    .map((c) => `[${c.id}] ${sectionLabel(c.docTitle, c.sectionPath)} (p.${c.pageStart}, ${c.kind})\n${c.content}`) // whole chunks: facts often sit late
    .join("\n\n");
  return (
    "Score how well each passage answers the question: 3 = answers it directly, 2 = contains needed facts, " +
    "1 = related but not useful, 0 = unrelated. Score every passage by its [id].\n\n" +
    `Question: ${question}\n\nPassages:\n\n${passages}`
  );
}

/** One call scores all candidates 0–3 (spec §6.4). */
export async function rerank(client: GenClient, model: string, question: string, candidates: Candidate[]): Promise<Map<number, number>> {
  const answer = await generateJson<{ scores: { id: number; score: number }[] }>(client, model, rerankPrompt(question, candidates), SCHEMA);
  return new Map(answer.scores.map(({ id, score }) => [id, Math.max(0, Math.min(3, Math.round(score)))]));
}

/** Best-scored first (ties keep search order); only passages scoring ≥ MIN_RELEVANCE, at most KEEP. */
export function selectRelevant(candidates: Candidate[], scores: Map<number, number>): (Candidate & { relevance: number })[] {
  return candidates
    .map((c) => ({ ...c, relevance: scores.get(c.id) ?? 0 }))
    .filter((c) => c.relevance >= MIN_RELEVANCE)
    .sort((a, b) => b.relevance - a.relevance) // stable: ties keep search order
    .slice(0, KEEP);
}

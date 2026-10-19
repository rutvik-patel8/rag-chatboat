import { numbersIn } from "@/lib/rag/numbers";

export type SourceText = { id: number; label: string; text: string };

const CITATION = /\[(S\d+(?:\s*[,;]\s*S?\d+)*)\]/g; // [S1], also [S1, S2] and [S1; 3]

/** Source numbers an answer cites, in order, including grouped citations. */
export function citedIds(answer: string): number[] {
  return [...answer.matchAll(CITATION)].flatMap((m) => m[1].split(/[,;]/).map((id) => Number(id.trim().replace(/^S/, ""))));
}

/** Deterministic checks after generation: citations exist and every figure appears in a cited source. */
export function verifyAnswer(answer: string, sources: SourceText[]): string[] {
  if (answer.trim().startsWith("I couldn't find this")) return []; // a refusal has nothing to cite
  const warnings: string[] = [];
  const cited = citedIds(answer);
  const known = new Set(sources.map((source) => source.id));
  const unknown = [...new Set(cited.filter((id) => !known.has(id)))];
  if (cited.length === 0) warnings.push("no citations");
  if (unknown.length) warnings.push(`cites unknown sources: ${unknown.map((id) => `S${id}`).join(", ")}`);
  const supported = new Set(
    sources.filter((source) => cited.includes(source.id)).flatMap((source) => numbersIn(`${source.label} ${source.text}`)),
  );
  const unsupported = [...new Set(numbersIn(answer).filter((value) => !supported.has(value)))];
  if (unsupported.length) warnings.push(`unverified figures: ${unsupported.join(", ")}`);
  return warnings;
}

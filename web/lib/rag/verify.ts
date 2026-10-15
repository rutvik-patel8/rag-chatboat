import { numbersIn } from "@/lib/rag/numbers";

export type SourceText = { id: number; label: string; text: string };

/** Deterministic checks after generation: citations exist and every figure appears in a cited source. */
export function verifyAnswer(answer: string, sources: SourceText[]): string[] {
  const warnings: string[] = [];
  const cited = [...answer.matchAll(/\[S(\d+)\]/g)].map((match) => Number(match[1]));
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

import type { EvalQuestion } from "@/lib/eval/questions";
import type { SourceInfo } from "@/lib/rag/answer";

// Per-question scorecard (spec §8.2). Each metric is null when it does not apply to the question's type.

export type Outcome = { answer: string; sources: SourceInfo[]; warnings: string[]; error?: string };
export type Score = {
  hit: boolean | null; // an expected page or section among the final sources
  facts: boolean | null; // every must_contain present after normalization
  citation: number | null; // share of cited sources on expected pages/sections
  verifier: boolean | null; // no verifier warnings
  refused: boolean | null; // traps: the fixed refusal message
  isolated: boolean | null; // isolation: only the expected section (or annex rows) cited, no forbidden figures
  pass: boolean;
};

const REFUSAL = "i couldn't find this"; // the gate's fixed message and the prompt's refusal rule both start this way
const CITATION = /\[(S\d+(?:\s*[,;]\s*S?\d+)*)\]/g;

/** Lowercase, unify dashes, drop thousands separators and write "percent" as "%" so "6.3 percent" matches "6.3%". */
export function normalize(text: string): string {
  return text
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[−–—]/g, "-")
    .replace(/(\d),(?=\d{3}\b)/g, "$1")
    .replace(/\s*\b(percent|per cent)\b/g, "%")
    .replace(/\s+%/g, "%")
    .replace(/\s+/g, " ")
    .trim();
}

export function citedIds(answer: string): number[] {
  return [...answer.matchAll(CITATION)].flatMap((m) => m[1].split(/[,;]/).map((id) => Number(id.trim().replace(/^S/, ""))));
}

const inSection = (path: string, section: string | undefined) => !!section && (path === section || path.startsWith(`${section} › `));

/** A source counts as relevant when it sits in the expected section or covers an expected page. */
export function relevant(source: SourceInfo, q: EvalQuestion): boolean {
  return inSection(source.sectionPath, q.expected_section) || q.expected_pages.some((p) => p >= source.pageStart && p <= source.pageEnd);
}

export function scoreAnswer(q: EvalQuestion, outcome: Outcome): Score {
  const { answer, sources, warnings, error } = outcome;
  const text = normalize(answer);
  const verifier = error ? null : warnings.filter((w) => !w.startsWith("reranking failed")).length === 0;
  if (q.type === "trap") {
    const refused = !error && text.startsWith(REFUSAL);
    return { hit: null, facts: null, citation: null, verifier: null, refused, isolated: null, pass: refused };
  }
  const hit = sources.some((s) => relevant(s, q));
  const facts = !error && q.must_contain.every((fact) => text.includes(normalize(fact)));
  const cited = [...new Set(citedIds(answer))].map((id) => sources.find((s) => s.id === id)).filter((s) => s !== undefined);
  const citation = cited.length ? cited.filter((s) => relevant(s, q)).length / cited.length : null;
  let isolated: boolean | null = null;
  if (q.type === "isolation") {
    const onlyExpected = sources.every((s) => inSection(s.sectionPath, q.expected_section) || inSection(s.sectionPath, "Statistical Annex"));
    isolated = onlyExpected && !q.must_not_contain.some((fact) => text.includes(normalize(fact)));
  }
  const pass = hit && facts && citation !== null && citation > 0 && isolated !== false;
  return { hit, facts, citation, verifier, refused: null, isolated, pass };
}

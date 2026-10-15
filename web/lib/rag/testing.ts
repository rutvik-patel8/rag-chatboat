import type { Candidate } from "@/lib/rag/retrieve";

/** Test helper: a search candidate with sensible defaults. */
export function candidate(id: number, overrides: Partial<Candidate> = {}): Candidate {
  return {
    id, docSlug: "gep", docTitle: "Global Economic Prospects", sectionId: 10, sectionPath: "Ch. 2 Regional Outlooks › South Asia",
    seq: id, kind: "text", content: `passage ${id}`, pageStart: 96, pageEnd: 96, footnotes: [], score: 1 / (60 + id),
    vecRank: id, kwRank: null, ...overrides,
  };
}

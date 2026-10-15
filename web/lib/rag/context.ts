import type { Pool } from "pg";
import type { Candidate } from "@/lib/rag/retrieve";

export const CONTEXT_CHARS = 24_000; // ≈ 6k tokens of sources (spec §6.5)

export type SourceBlock = {
  id: number; // S1, S2, … in the prompt
  label: string;
  text: string;
  docSlug: string;
  sectionPath: string;
  pageStart: number;
  pageEnd: number;
  kind: string;
  chunkIds: number[];
};

type Neighbour = { id: number; section_id: number; seq: number; content: string; page_start: number; page_end: number };

/** GEP text chunks next to each picked GEP text chunk in the same section (seq ± 1), keyed by "section:seq".
 * Only GEP (spec §6.5): its child chunks split long sections; GPO chunks already overlap. */
export async function loadNeighbours(pool: Pool, picked: Candidate[]): Promise<Map<string, Neighbour>> {
  const texts = picked.filter((c) => c.kind === "text" && c.docSlug === "gep");
  if (!texts.length) return new Map();
  const { rows } = await pool.query<Neighbour>(
    `SELECT c.id, c.section_id, c.seq, c.content, c.page_start, c.page_end
     FROM chunks c JOIN unnest($1::int[], $2::int[]) AS p(section_id, seq) ON c.section_id = p.section_id
     WHERE c.kind = 'text' AND c.seq IN (p.seq - 1, p.seq + 1)`,
    [texts.map((c) => c.sectionId), texts.map((c) => c.seq)],
  );
  return new Map(rows.map((row) => [`${row.section_id}:${row.seq}`, row]));
}

/** "Doc › Section", or the document alone for text before the first heading (empty path). */
export function sectionLabel(docTitle: string, sectionPath: string): string {
  return sectionPath ? `${docTitle} › ${sectionPath}` : docTitle;
}

export function buildBlocks(
  picked: Candidate[], neighbours: Map<string, Neighbour>, budget = CONTEXT_CHARS, { footnotes: withFootnotes = true } = {},
): SourceBlock[] {
  const used = new Set(picked.map((c) => c.id));
  const blocks: SourceBlock[] = [];
  let size = 0;
  for (const c of picked) {
    const own = { content: c.content, id: c.id, start: c.pageStart, end: c.pageEnd };
    const around = (seq: number) => {
      const n = c.kind === "text" ? neighbours.get(`${c.sectionId}:${seq}`) : undefined;
      return n && !used.has(n.id) ? [{ content: n.content, id: n.id, start: n.page_start, end: n.page_end }] : [];
    };
    const footnotes = withFootnotes ? c.footnotes.map((f) => `${f.n}. ${f.text}`).join("\n") : "";
    const render = (ps: (typeof own)[]) => ps.map((p) => p.content).join("\n\n") + (footnotes ? `\n\nFootnotes:\n${footnotes}` : "");
    let parts = [...around(c.seq - 1), own, ...around(c.seq + 1)];
    let text = render(parts);
    if (blocks.length && size + text.length > budget) {
      parts = [own]; // drop the neighbours before dropping a scored pick
      text = render(parts);
      if (size + text.length > budget) break; // keep the best-ranked blocks within budget
    }
    parts.forEach((p) => used.add(p.id));
    size += text.length;
    const pageStart = Math.min(...parts.map((p) => p.start));
    const pageEnd = Math.max(...parts.map((p) => p.end));
    const pages = pageStart === pageEnd ? `p.${pageStart}` : `p.${pageStart}-${pageEnd}`;
    const id = blocks.length + 1;
    blocks.push({
      id, label: `[S${id}] ${sectionLabel(c.docTitle, c.sectionPath)} | ${pages} | ${c.kind}`, text,
      docSlug: c.docSlug, sectionPath: c.sectionPath, pageStart, pageEnd, kind: c.kind, chunkIds: parts.map((p) => p.id),
    });
  }
  return blocks;
}

export function formatSources(blocks: SourceBlock[]): string {
  return blocks.map((b) => `${b.label}\n${b.text}`).join("\n\n---\n\n");
}

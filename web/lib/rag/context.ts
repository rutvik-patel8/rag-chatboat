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

/** Text chunks next to each picked text chunk in the same section (seq ± 1), keyed by "section:seq". */
export async function loadNeighbours(pool: Pool, picked: Candidate[]): Promise<Map<string, Neighbour>> {
  const texts = picked.filter((c) => c.kind === "text");
  if (!texts.length) return new Map();
  const { rows } = await pool.query<Neighbour>(
    `SELECT c.id, c.section_id, c.seq, c.content, c.page_start, c.page_end
     FROM chunks c JOIN unnest($1::int[], $2::int[]) AS p(section_id, seq) ON c.section_id = p.section_id
     WHERE c.kind = 'text' AND c.seq IN (p.seq - 1, p.seq + 1)`,
    [texts.map((c) => c.sectionId), texts.map((c) => c.seq)],
  );
  return new Map(rows.map((row) => [`${row.section_id}:${row.seq}`, row]));
}

export function buildBlocks(picked: Candidate[], neighbours: Map<string, Neighbour>, budget = CONTEXT_CHARS): SourceBlock[] {
  const used = new Set(picked.map((c) => c.id));
  const blocks: SourceBlock[] = [];
  let size = 0;
  for (const c of picked) {
    const parts = [{ content: c.content, id: c.id, start: c.pageStart, end: c.pageEnd }];
    if (c.kind === "text") {
      for (const [seq, place] of [[c.seq - 1, "before"], [c.seq + 1, "after"]] as const) {
        const n = neighbours.get(`${c.sectionId}:${seq}`);
        if (n && !used.has(n.id)) {
          used.add(n.id);
          const part = { content: n.content, id: n.id, start: n.page_start, end: n.page_end };
          if (place === "before") parts.unshift(part);
          else parts.push(part);
        }
      }
    }
    const footnotes = c.footnotes.map((f) => `${f.n}. ${f.text}`).join("\n");
    const text = parts.map((p) => p.content).join("\n\n") + (footnotes ? `\n\nFootnotes:\n${footnotes}` : "");
    if (blocks.length && size + text.length > budget) break; // keep the best-ranked blocks within budget
    size += text.length;
    const pageStart = Math.min(...parts.map((p) => p.start));
    const pageEnd = Math.max(...parts.map((p) => p.end));
    const pages = pageStart === pageEnd ? `p.${pageStart}` : `p.${pageStart}-${pageEnd}`;
    const id = blocks.length + 1;
    blocks.push({
      id, label: `[S${id}] ${c.docTitle} › ${c.sectionPath} | ${pages} | ${c.kind}`, text,
      docSlug: c.docSlug, sectionPath: c.sectionPath, pageStart, pageEnd, kind: c.kind, chunkIds: parts.map((p) => p.id),
    });
  }
  return blocks;
}

export function formatSources(blocks: SourceBlock[]): string {
  return blocks.map((b) => `${b.label}\n${b.text}`).join("\n\n---\n\n");
}

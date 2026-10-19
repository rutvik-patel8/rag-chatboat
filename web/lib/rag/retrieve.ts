import type { Pool } from "pg";
import type { Catalog } from "@/lib/rag/router";

export type Candidate = {
  id: number;
  docSlug: string;
  docTitle: string;
  sectionId: number;
  sectionPath: string;
  seq: number;
  kind: string;
  content: string;
  pageStart: number;
  pageEnd: number;
  footnotes: { n: number; text: string }[];
  score: number; // reciprocal rank fusion
  vecRank: number | null;
  kwRank: number | null;
};

export type SearchArgs = {
  vector: number[]; question: string; docs: string[]; paths: string[]; limit?: number;
  keyword?: boolean; // false: vector search only (the vectorOnly ablation)
};

/** Keyword side of hybrid search: any question word may match (OR), ranked by coverage. */
export function keywordQuery(question: string): string {
  const words = question.toLowerCase().match(/[a-z0-9][a-z0-9.%-]*[a-z0-9%]|\d/g) ?? [];
  return [...new Set(words.filter((word) => word.length >= 3 || /\d/.test(word)))].join(" or ");
}

// Vector top 40 ∪ keyword top 40 inside the routed documents/sections, fused with RRF (k = 60). Spec §6.3.
const HYBRID_SQL = `
WITH q AS (SELECT $1::vector AS v, websearch_to_tsquery('english', $2) AS ts),
scoped AS (
  SELECT c.id, c.embedding, c.tsv FROM chunks c
  JOIN documents d ON d.id = c.doc_id JOIN sections s ON s.id = c.section_id
  WHERE d.slug = ANY($3::text[])
    AND ($4::text[] IS NULL OR s.path = ANY($4::text[])
         OR EXISTS (SELECT 1 FROM unnest($4::text[]) AS p WHERE starts_with(s.path, p || ' › ')))
),
vec AS (
  SELECT id, row_number() OVER (ORDER BY embedding <=> (SELECT v FROM q)) AS r
  FROM scoped ORDER BY embedding <=> (SELECT v FROM q) LIMIT 40
),
kw AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, (SELECT ts FROM q)) DESC) AS r
  FROM scoped WHERE tsv @@ (SELECT ts FROM q) ORDER BY ts_rank_cd(tsv, (SELECT ts FROM q)) DESC LIMIT 40
),
fused AS (SELECT id, sum(1.0 / (60 + r)) AS score FROM (SELECT * FROM vec UNION ALL SELECT * FROM kw) u GROUP BY id)
SELECT c.id, d.slug AS doc_slug, d.title AS doc_title, s.id AS section_id, s.path AS section_path, c.seq, c.kind,
       c.content, c.page_start, c.page_end, c.footnotes, f.score, v.r AS vec_rank, k.r AS kw_rank
FROM fused f
JOIN chunks c ON c.id = f.id JOIN sections s ON s.id = c.section_id JOIN documents d ON d.id = c.doc_id
LEFT JOIN vec v ON v.id = f.id LEFT JOIN kw k ON k.id = f.id
ORDER BY f.score DESC LIMIT $5`;

type Row = {
  id: number; doc_slug: string; doc_title: string; section_id: number; section_path: string; seq: number; kind: string;
  content: string; page_start: number; page_end: number; footnotes: { n: number; text: string }[]; score: string;
  vec_rank: string | null; kw_rank: string | null;
};

export function toCandidate(row: Row): Candidate {
  return {
    id: row.id, docSlug: row.doc_slug, docTitle: row.doc_title, sectionId: row.section_id, sectionPath: row.section_path,
    seq: row.seq, kind: row.kind, content: row.content, pageStart: row.page_start, pageEnd: row.page_end,
    footnotes: row.footnotes ?? [], score: Number(row.score),
    vecRank: row.vec_rank === null ? null : Number(row.vec_rank), kwRank: row.kw_rank === null ? null : Number(row.kw_rank),
  };
}

export async function hybridSearch(pool: Pool, { vector, question, docs, paths, limit = 20, keyword = true }: SearchArgs): Promise<Candidate[]> {
  const { rows } = await pool.query<Row>(HYBRID_SQL, [
    `[${vector.join(",")}]`, keyword ? keywordQuery(question) : null, docs, paths.length ? paths : null, limit, // NULL query: no keyword rows
  ]);
  return rows.map(toCandidate);
}

export async function loadCatalog(pool: Pool): Promise<Catalog> {
  const { rows } = await pool.query<{ slug: string; title: string; path: string; level: number; kind: string }>(
    `SELECT d.slug, d.title, s.path, s.level, s.kind FROM sections s JOIN documents d ON d.id = s.doc_id ORDER BY d.slug, s.id`,
  );
  const catalog: Catalog = {};
  for (const row of rows) {
    catalog[row.slug] ??= { title: row.title, sections: [] };
    catalog[row.slug].sections.push({ path: row.path, level: row.level, kind: row.kind });
  }
  return catalog;
}

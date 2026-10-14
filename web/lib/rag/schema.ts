import type { Pool } from "pg";

export type SchemaFacts = {
  vectorType: string | null;
  documents: { slug: string; embed_model: string; embed_dim: number }[];
};

export type ExpectedEmbedding = { embedModel: string; embedDim: number };

/** Problems that would make retrieval silently wrong; empty array means healthy. */
export function evaluateSchema(facts: SchemaFacts, expected: ExpectedEmbedding): string[] {
  const problems: string[] = [];
  if (facts.vectorType === null) {
    problems.push("chunks.embedding not found; apply db/schema.sql with db/reset.sh");
  } else if (facts.vectorType !== `vector(${expected.embedDim})`) {
    problems.push(`chunks.embedding is ${facts.vectorType} but EMBED_DIM is ${expected.embedDim}; run db/reset.sh`);
  }
  for (const doc of facts.documents) {
    if (doc.embed_model !== expected.embedModel || doc.embed_dim !== expected.embedDim) {
      problems.push(
        `document "${doc.slug}" was embedded with ${doc.embed_model}/${doc.embed_dim} but the server uses ` +
          `${expected.embedModel}/${expected.embedDim}; re-run ingestion`,
      );
    }
  }
  return problems;
}

export async function readSchemaFacts(pool: Pool): Promise<SchemaFacts> {
  const column = await pool.query<{ type: string }>(
    `SELECT format_type(atttypid, atttypmod) AS type FROM pg_attribute
     WHERE attrelid = to_regclass('public.chunks') AND attname = 'embedding'`,
  );
  const vectorType = column.rows[0]?.type ?? null;
  if (vectorType === null) return { vectorType, documents: [] };
  const docs = await pool.query<SchemaFacts["documents"][number]>(
    "SELECT slug, embed_model, embed_dim FROM documents ORDER BY slug",
  );
  return { vectorType, documents: docs.rows };
}

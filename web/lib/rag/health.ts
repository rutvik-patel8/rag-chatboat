import { evaluateSchema, type ExpectedEmbedding, type SchemaFacts } from "@/lib/rag/schema";

export type HealthResult = {
  status: 200 | 500 | 503;
  body: { ok: boolean; problems: string[]; documents: string[] };
};

/** Node's connection errors are often AggregateErrors with an empty message but a `code`. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message || (err as { code?: string }).code || err.name;
  return String(err);
}

export async function runHealthCheck(
  readFacts: () => Promise<SchemaFacts>,
  expected: ExpectedEmbedding,
): Promise<HealthResult> {
  let facts: SchemaFacts;
  try {
    facts = await readFacts();
  } catch (err) {
    return {
      status: 503,
      body: { ok: false, problems: [`Database unavailable: ${errorMessage(err)}`], documents: [] },
    };
  }
  const problems = evaluateSchema(facts, expected);
  return {
    status: problems.length === 0 ? 200 : 500,
    body: { ok: problems.length === 0, problems, documents: facts.documents.map((doc) => doc.slug) },
  };
}

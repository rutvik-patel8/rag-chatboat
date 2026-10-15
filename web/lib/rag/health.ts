import { evaluateSchema, type ExpectedEmbedding, type SchemaFacts } from "@/lib/rag/schema";

export type HealthResult = {
  status: 200 | 500 | 503;
  body: { ok: boolean; problems: string[]; documents: string[] };
};

/** Node's connection errors are often AggregateErrors with an empty message but a `code`. */
export function errorMessage(err: unknown): string {
  if (!(err instanceof Error)) return String(err);
  return geminiMessage(err.message) ?? (err.message || (err as { code?: string }).code || err.name);
}

/** @google/genai puts the whole JSON body in the message: {"error":{"code":503,"message":"…","status":"UNAVAILABLE"}}. */
function geminiMessage(message: string): string | undefined {
  try {
    const { error } = JSON.parse(message) as { error?: { code?: number; message?: string; status?: string } };
    return error?.message ? `${error.message} (${[error.code, error.status].filter(Boolean).join(" ")})` : undefined;
  } catch {
    return undefined;
  }
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

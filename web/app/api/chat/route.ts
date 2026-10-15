import { NextResponse } from "next/server";
import { getEnv } from "@/lib/env";
import { answer } from "@/lib/rag/answer";
import { getPool } from "@/lib/rag/db";
import { productionDeps } from "@/lib/rag/deps";
import { errorMessage } from "@/lib/rag/health";
import { ndjsonStream } from "@/lib/rag/ndjson";
import { parseChatRequest } from "@/lib/rag/request";
import { evaluateSchema, readSchemaFacts } from "@/lib/rag/schema";

let schemaCheck: Promise<string[]> | undefined; // spec §7: refuse to answer if embeddings don't match the DB

function schemaProblems(): Promise<string[]> {
  const env = getEnv();
  schemaCheck ??= readSchemaFacts(getPool())
    .then((facts) => evaluateSchema(facts, { embedModel: env.GEMINI_EMBED_MODEL, embedDim: env.EMBED_DIM }))
    .catch((error) => {
      schemaCheck = undefined; // retry on the next request once the DB is back
      return [`Database unavailable: ${errorMessage(error)}`];
    });
  return schemaCheck;
}

export async function POST(req: Request) {
  const parsed = parseChatRequest(await req.json().catch(() => null));
  if (!parsed.ok) return NextResponse.json({ error: parsed.error }, { status: 400 });
  const problems = await schemaProblems();
  if (problems.length) return NextResponse.json({ error: problems.join("; ") }, { status: 503 });
  const stream = ndjsonStream((emit) => answer(parsed.request, productionDeps(), emit));
  return new Response(stream, { headers: { "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store" } });
}

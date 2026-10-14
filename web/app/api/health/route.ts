import { connection, NextResponse } from "next/server";
import { getEnv, type Env } from "@/lib/env";
import { getPool } from "@/lib/rag/db";
import { errorMessage, runHealthCheck } from "@/lib/rag/health";
import { readSchemaFacts } from "@/lib/rag/schema";

export async function GET() {
  await connection(); // always run at request time (Cache Components would otherwise try to prerender)
  let env: Env;
  try {
    env = getEnv();
  } catch (err) {
    return NextResponse.json({ ok: false, problems: [errorMessage(err)], documents: [] }, { status: 500 });
  }
  const { status, body } = await runHealthCheck(() => readSchemaFacts(getPool()), {
    embedModel: env.GEMINI_EMBED_MODEL,
    embedDim: env.EMBED_DIM,
  });
  return NextResponse.json(body, { status });
}

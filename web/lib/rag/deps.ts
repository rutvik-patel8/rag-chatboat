import { getEnv, utilityModel } from "@/lib/env";
import type { Deps } from "@/lib/rag/answer";
import { loadNeighbours } from "@/lib/rag/context";
import { getPool } from "@/lib/rag/db";
import { embedQuery, getGenAI } from "@/lib/rag/gemini";
import { generateText, streamText, type GenClient } from "@/lib/rag/genai";
import { condensePrompt } from "@/lib/rag/prompt";
import { rerank } from "@/lib/rag/rerank";
import { hybridSearch, loadCatalog } from "@/lib/rag/retrieve";
import type { Catalog } from "@/lib/rag/router";

/** Load the catalog once; a failed or empty load (DB down, nothing ingested yet) is retried on the next request. */
export function memoCatalog(load: () => Promise<Catalog>): () => Promise<Catalog> {
  let cached: Promise<Catalog> | undefined;
  return () => {
    const pending = (cached ??= load());
    pending.then((c) => { if (!Object.keys(c).length) cached = undefined; }, () => { cached = undefined; });
    return pending;
  };
}

const catalog = memoCatalog(() => loadCatalog(getPool()));

/**
 * Production wiring: Postgres, Gemini embeddings, the utility model for helpers and the chat model for answers.
 * `signal` (the client went away) cancels the Gemini calls of this request; `chatModel` overrides the answer model
 * (the evaluation's ablation runs answer with the utility model).
 */
export function productionDeps(signal?: AbortSignal, { chatModel }: { chatModel?: string } = {}): Deps {
  const env = getEnv();
  const client = getGenAI() as unknown as GenClient;
  const helper = utilityModel(env);
  return {
    catalog,
    condense: async (history, question) => {
      if (!history.length) return question;
      try {
        return await generateText(client, helper, condensePrompt(history, question), { signal });
      } catch {
        return question; // a failed rewrite should not block the answer
      }
    },
    embed: embedQuery,
    search: (args) => hybridSearch(getPool(), args),
    rerank: (question, candidates) => rerank(client, helper, question, candidates, signal),
    neighbours: (picked) => loadNeighbours(getPool(), picked),
    generate: (system, prompt) => streamText(client, chatModel ?? env.GEMINI_CHAT_MODEL, system, prompt, { signal }),
  };
}

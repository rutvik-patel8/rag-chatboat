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

let catalog: Promise<Catalog> | undefined;

/** Production wiring: Postgres, Gemini embeddings, the utility model for helpers and the chat model for answers. */
export function productionDeps(): Deps {
  const env = getEnv();
  const client = getGenAI() as unknown as GenClient;
  const helper = utilityModel(env);
  return {
    catalog: () => (catalog ??= loadCatalog(getPool())),
    condense: async (history, question) => {
      if (!history.length) return question;
      try {
        return await generateText(client, helper, condensePrompt(history, question));
      } catch {
        return question; // a failed rewrite should not block the answer
      }
    },
    embed: embedQuery,
    search: (args) => hybridSearch(getPool(), args),
    rerank: (question, candidates) => rerank(client, helper, question, candidates),
    neighbours: (picked) => loadNeighbours(getPool(), picked),
    generate: (system, prompt) => streamText(client, env.GEMINI_CHAT_MODEL, system, prompt),
  };
}

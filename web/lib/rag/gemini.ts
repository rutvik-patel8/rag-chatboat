import { GoogleGenAI } from "@google/genai";
import { getEnv } from "@/lib/env";

export type EmbedTaskType = "RETRIEVAL_DOCUMENT" | "RETRIEVAL_QUERY";
export const MAX_BATCH = 100;
const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);

export function isRetryable(err: unknown): boolean {
  const status = (err as { status?: unknown } | null)?.status; // @google/genai ApiError carries the HTTP status
  if (typeof status === "number") return RETRYABLE_STATUS.has(status);
  return err instanceof TypeError; // fetch network failures surface as TypeError
}

type RetryOptions = { attempts?: number; baseDelayMs?: number; sleep?: (ms: number) => Promise<void> };
const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function withRetry<T>(
  fn: () => Promise<T>,
  { attempts = 3, baseDelayMs = 1000, sleep = realSleep }: RetryOptions = {},
): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt >= attempts || !isRetryable(err)) throw err;
      await sleep(baseDelayMs * 2 ** (attempt - 1));
    }
  }
}

export function l2Normalize(v: number[]): number[] {
  const norm = Math.sqrt(v.reduce((sum, x) => sum + x * x, 0));
  if (norm === 0) throw new Error("cannot normalise a zero vector");
  return v.map((x) => x / norm);
}

export type EmbedClient = {
  models: {
    embedContent(req: {
      model: string;
      contents: string[];
      config: { taskType: EmbedTaskType; outputDimensionality: number };
    }): Promise<{ embeddings?: { values?: number[] }[] }>;
  };
};

export type Embedder = { embed(texts: string[], taskType: EmbedTaskType): Promise<number[][]> };

export function createEmbedder({
  client,
  model,
  dim,
  sleep,
}: {
  client: EmbedClient;
  model: string;
  dim: number;
  sleep?: RetryOptions["sleep"];
}): Embedder {
  return {
    async embed(texts, taskType) {
      if (texts.length === 0) return [];
      if (texts.length > MAX_BATCH) throw new Error(`embed accepts at most ${MAX_BATCH} texts, got ${texts.length}`);
      if (texts.some((text) => text.trim() === "")) throw new Error("cannot embed empty text");
      const response = await withRetry(
        () => client.models.embedContent({ model, contents: texts, config: { taskType, outputDimensionality: dim } }),
        { sleep },
      );
      const vectors = (response.embeddings ?? []).map((embedding) => embedding.values ?? []);
      if (vectors.length !== texts.length || vectors.some((v) => v.length !== dim)) {
        throw new Error(`expected ${texts.length} embeddings of ${dim} dims from ${model}`);
      }
      return vectors.map(l2Normalize);
    },
  };
}

let genAI: GoogleGenAI | undefined;
let embedder: Embedder | undefined;

export function getGenAI(): GoogleGenAI {
  genAI ??= new GoogleGenAI({ apiKey: getEnv().GEMINI_API_KEY });
  return genAI;
}

export function getEmbedder(): Embedder {
  const env = getEnv();
  embedder ??= createEmbedder({ client: getGenAI(), model: env.GEMINI_EMBED_MODEL, dim: env.EMBED_DIM });
  return embedder;
}

export async function embedQuery(text: string): Promise<number[]> {
  const [vector] = await getEmbedder().embed([text], "RETRIEVAL_QUERY");
  return vector;
}

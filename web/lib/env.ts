import { z } from "zod";

// Server-only settings. None are NEXT_PUBLIC_, so they never reach the browser bundle.
const EnvSchema = z.object({
  GEMINI_API_KEY: z.string().trim().min(1),
  DATABASE_URL: z.string().trim().min(1),
  GEMINI_CHAT_MODEL: z.string().trim().min(1),
  GEMINI_EMBED_MODEL: z.string().trim().min(1),
  EMBED_DIM: z.coerce.number().int().min(128).max(3072),
  GEMINI_SUMMARY_MODEL: z.string().trim().optional(),
  GEMINI_UTILITY_MODEL: z.string().trim().optional(), // reranking and follow-up rewriting
});

export type Env = z.infer<typeof EnvSchema>;

export class ConfigError extends Error {}

export function parseEnv(source: Record<string, string | undefined>): Env {
  const result = EnvSchema.safeParse(source);
  if (!result.success) {
    const names = [...new Set(result.error.issues.map((issue) => String(issue.path[0])))];
    throw new ConfigError(`Invalid or missing settings in .env: ${names.join(", ")} (see .env.example)`);
  }
  return result.data;
}

/** Model for small helper calls: GEMINI_UTILITY_MODEL, else the summary model, else the chat model. */
export function utilityModel(env: Env): string {
  return env.GEMINI_UTILITY_MODEL || env.GEMINI_SUMMARY_MODEL || env.GEMINI_CHAT_MODEL;
}

let cached: Env | undefined;

export function getEnv(): Env {
  cached ??= parseEnv(process.env);
  return cached;
}

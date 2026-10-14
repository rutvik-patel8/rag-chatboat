import { z } from "zod";

// Server-only settings. None are NEXT_PUBLIC_, so they never reach the browser bundle.
const EnvSchema = z.object({
  GEMINI_API_KEY: z.string().trim().min(1),
  DATABASE_URL: z.string().trim().min(1),
  GEMINI_CHAT_MODEL: z.string().trim().min(1),
  GEMINI_EMBED_MODEL: z.string().trim().min(1),
  EMBED_DIM: z.coerce.number().int().min(128).max(3072),
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

let cached: Env | undefined;

export function getEnv(): Env {
  cached ??= parseEnv(process.env);
  return cached;
}

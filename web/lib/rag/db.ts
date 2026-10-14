import { Pool } from "pg";
import { getEnv } from "@/lib/env";

// Cache on globalThis so Next.js dev hot reloads don't open a new pool each time.
const globalForPool = globalThis as unknown as { ragPool?: Pool };

export function getPool(): Pool {
  globalForPool.ragPool ??= new Pool({
    connectionString: getEnv().DATABASE_URL,
    max: 5,
    connectionTimeoutMillis: 3000,
  });
  return globalForPool.ragPool;
}

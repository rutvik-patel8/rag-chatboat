import { Pool } from "pg";
import { getEnv } from "@/lib/env";
import { errorMessage } from "@/lib/rag/health";

export function createPool(connectionString: string): Pool {
  const pool = new Pool({ connectionString, max: 5, connectionTimeoutMillis: 3000 });
  // Idle clients emit 'error' when the DB restarts; without a listener Node treats it as uncaught and exits.
  pool.on("error", (err) => console.warn(`pg idle client error: ${errorMessage(err)}`));
  return pool;
}

// Cache on globalThis so Next.js dev hot reloads don't open a new pool each time.
const globalForPool = globalThis as unknown as { ragPool?: Pool };

export function getPool(): Pool {
  globalForPool.ragPool ??= createPool(getEnv().DATABASE_URL);
  return globalForPool.ragPool;
}

import { describe, expect, it } from "vitest";
import { ConfigError, parseEnv } from "@/lib/env";

const VALID = {
  GEMINI_API_KEY: "test-key",
  DATABASE_URL: "postgresql://rag:rag@localhost:5433/rag",
  GEMINI_CHAT_MODEL: "gemini-3.8-flash",
  GEMINI_EMBED_MODEL: "gemini-embedding-001",
  EMBED_DIM: "768",
};

describe("parseEnv", () => {
  it("parses valid settings and coerces EMBED_DIM to a number", () => {
    const env = parseEnv(VALID);
    expect(env.EMBED_DIM).toBe(768);
    expect(env.GEMINI_EMBED_MODEL).toBe("gemini-embedding-001");
  });

  it("names every blank or missing variable in one error", () => {
    const { DATABASE_URL: _omit, ...rest } = VALID;
    const run = () => parseEnv({ ...rest, GEMINI_API_KEY: "   " });
    expect(run).toThrow(ConfigError);
    expect(run).toThrow("GEMINI_API_KEY, DATABASE_URL");
  });

  it("rejects a non-numeric or out-of-range EMBED_DIM", () => {
    expect(() => parseEnv({ ...VALID, EMBED_DIM: "abc" })).toThrow("EMBED_DIM");
    expect(() => parseEnv({ ...VALID, EMBED_DIM: "4096" })).toThrow("EMBED_DIM");
  });
});

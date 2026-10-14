import { afterEach, describe, expect, it, vi } from "vitest";
import { createPool } from "@/lib/rag/db";

describe("createPool", () => {
  afterEach(() => vi.restoreAllMocks());

  it("survives an idle-client error (e.g. DB restart) instead of throwing an unhandled 'error' event", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const pool = createPool("postgresql://rag:rag@localhost:1/rag");
    expect(() => pool.emit("error", new Error("terminating connection due to administrator command"))).not.toThrow();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("terminating connection"));
    await pool.end();
  });
});

import { describe, expect, it } from "vitest";
import { keywordQuery } from "@/lib/rag/retrieve";

describe("keywordQuery", () => {
  it("ORs the question's words, keeping numbers and dropping short words", () => {
    expect(keywordQuery("How much did GPO procure in FY 2018 for agencies?")).toBe("how or much or did or gpo or procure or 2018 or for or agencies");
    expect(keywordQuery("ENERGY STAR 58% less")).toBe("energy or star or 58% or less");
  });
});

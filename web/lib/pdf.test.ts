import { describe, expect, it } from "vitest";
import { GET } from "@/app/api/pdf/[slug]/route";
import { pdfPath } from "@/lib/pdf";

const get = (slug: string) => GET(new Request(`http://test/api/pdf/${slug}`), { params: Promise.resolve({ slug }) });

describe("PDF route", () => {
  it("serves a known document inline as a PDF", async () => {
    const res = await get("imac");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("content-disposition")).toBe('inline; filename="imac.pdf"');
    expect(new TextDecoder().decode((await res.arrayBuffer()).slice(0, 5))).toBe("%PDF-");
  });

  it("returns 404 for unknown slugs and never resolves paths from user input", async () => {
    for (const slug of ["nope", "../.env", "constructor", "__proto__"]) {
      expect(pdfPath(slug)).toBeNull();
      expect((await get(slug)).status).toBe(404);
    }
  });
});

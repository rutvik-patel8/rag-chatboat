import { readFile } from "node:fs/promises";
import { NextResponse } from "next/server";
import { pdfPath } from "@/lib/pdf";

export async function GET(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const file = pdfPath(slug);
  if (!file) return NextResponse.json({ error: `unknown document: ${slug}` }, { status: 404 });
  const body = await readFile(file).catch(() => null);
  if (!body) return NextResponse.json({ error: `PDF missing for ${slug}` }, { status: 404 });
  return new Response(new Uint8Array(body), {
    headers: { "Content-Type": "application/pdf", "Content-Disposition": `inline; filename="${slug}.pdf"`, "Cache-Control": "public, max-age=3600" },
  });
}

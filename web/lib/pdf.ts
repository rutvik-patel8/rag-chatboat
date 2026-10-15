import path from "node:path";

// The three fixed source PDFs (spec §1), served for "Open page" links. Only these slugs map to files.
export const PDF_FILES: Record<string, string> = {
  gpo: "GPO-Anual-report.pdf",
  gep: "GEP-Jun-2026.pdf",
  imac: "iMac_PER_Oct2024.pdf",
};

/** Absolute path of a document's PDF (repo-root docs/, one level above web/), or null for an unknown slug. */
export function pdfPath(slug: string): string | null {
  const file = Object.hasOwn(PDF_FILES, slug) ? PDF_FILES[slug] : undefined;
  return file ? path.resolve(process.cwd(), "..", "docs", file) : null;
}

import type { SourceInfo } from "@/lib/rag/answer";

// The answer model writes light Markdown (paragraphs, "- " / "1. " lists, **bold**) with [S#] citations.
// Rendered as plain React text: no HTML from the model or the PDFs is ever injected.

export type Inline = { type: "text"; text: string; strong: boolean } | { type: "cite"; id: number };
export type Block = { type: "p"; parts: Inline[] } | { type: "li"; marker: string; parts: Inline[] };

const CITATION = /\[(S\d+(?:\s*[,;]\s*S?\d+)*)\]/g; // [S1], also [S1, S2] and [S1; 3]
const LIST_ITEM = /^\s*([-*•]|\d+[.)])\s+(.*)$/;

/** Split text into runs and citation chips; a citation to a source that doesn't exist stays plain text. */
export function inlineParts(text: string, known: Set<number>): Inline[] {
  const parts: Inline[] = [];
  text.split(/\*\*(.+?)\*\*/g).forEach((segment, i) => {
    const strong = i % 2 === 1;
    let last = 0;
    for (const match of segment.matchAll(CITATION)) {
      const ids = match[1].split(/[,;]/).map((id) => Number(id.trim().replace(/^S/, "")));
      if (!ids.every((id) => known.has(id))) continue;
      if (match.index > last) parts.push({ type: "text", text: segment.slice(last, match.index), strong });
      ids.forEach((id) => parts.push({ type: "cite", id }));
      last = match.index + match[0].length;
    }
    if (last < segment.length) parts.push({ type: "text", text: segment.slice(last), strong });
  });
  return parts;
}

export function parseAnswer(text: string, known: Set<number>): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ type: "p", parts: inlineParts(paragraph.join(" "), known) });
    paragraph = [];
  };
  for (const line of text.split("\n")) {
    const item = line.match(LIST_ITEM);
    if (item) {
      flush();
      blocks.push({ type: "li", marker: /\d/.test(item[1]) ? item[1] : "•", parts: inlineParts(item[2], known) });
    } else if (line.trim()) {
      paragraph.push(line.trim());
    } else {
      flush();
    }
  }
  flush();
  return blocks;
}

export function pageLabel(source: Pick<SourceInfo, "pageStart" | "pageEnd">): string {
  return source.pageStart === source.pageEnd ? `p.${source.pageStart}` : `pp.${source.pageStart}–${source.pageEnd}`;
}

/** The source PDF opened at the passage's first page (browser PDF viewers honour #page=N). */
export function pdfHref(source: Pick<SourceInfo, "slug" | "pageStart">): string {
  return `/api/pdf/${encodeURIComponent(source.slug)}#page=${source.pageStart}`;
}

/** "GEP › Ch. 2 Regional Outlooks › South Asia", or the document alone for text before the first heading. */
export function sourceTitle(source: Pick<SourceInfo, "doc" | "sectionPath">): string {
  return source.sectionPath ? `${source.doc} › ${source.sectionPath}` : source.doc;
}

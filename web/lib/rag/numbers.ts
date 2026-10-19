// Figures in an answer must appear in the sources it cites (spec §6.8). Numbers are compared by value:
// "802,834" = "802834", "6.30" = "6.3", "−0.1" = "-0.1"; "58 percent" and "58%" both yield 58.
const NUMBER = /(?<![\w.])[-−–]?\d[\d,]*(?:\.\d+)?/g;
const CITATION = /\[S\d+\]/g;
const LIST_MARKER = /^\s*\d+[.)]\s+/gm; // "1. Trade tensions" numbers a list item, not a figure
// "footnote 18", "Table 2.9", "Box 1.1", "Figure 2.3.A", "Chapter 3" name a part of the report, not a figure
const REFERENCE = /\b(?:(?:foot|end)?notes?|figure|table|box|chapter|annex)\s+[A-Z]?\d+(?:\.\d+)*(?:\.[A-Z])?\b/gi;

export function numbersIn(text: string): string[] {
  const matches = text.replace(CITATION, " ").replace(REFERENCE, " ").replace(LIST_MARKER, "").match(NUMBER) ?? [];
  return matches.map((raw) => String(Number(raw.replace(/[−–]/, "-").replace(/,/g, ""))));
}

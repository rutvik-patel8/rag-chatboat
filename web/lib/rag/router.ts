// Deterministic query routing (spec §6.2): pick documents and hard section filters from explicit mentions.
// No LLM call — the reranker judges relevance; this only stops region/chapter questions from mixing sources.

export type Scope = "gpo" | "gep" | "imac" | "all";
export type Catalog = Record<string, { title: string; sections: { path: string; level: number; kind: string }[] }>;
export type Route = { docs: string[]; paths: string[] };

const DOC_HINTS: Record<string, RegExp> = {
  gpo: /\b(gpo|government publishing office|govinfo|fdsys|depository|federal register|congressional record|passports?)\b/i,
  imac: /\b(imac|apple|energy star|carbon footprint|recycled aluminum|packaging)\b/i,
  gep: /\b(world bank|gep|global economic prospects|emdes?|global (growth|outlook|economy)|regional outlooks?|sovereign debt|fiscal policy|commodit\w*|east asia|south asia|sub-saharan|latin america|middle east|central asia)\b/i,
};

export const REGIONS: [RegExp, string][] = [
  [/\b(east asia( and (the )?pacific)?|eap)\b/i, "East Asia and Pacific"],
  [/\b(europe and central asia|eca)\b/i, "Europe and Central Asia"],
  [/\b(latin america( and the caribbean)?|lac)\b/i, "Latin America and the Caribbean"],
  [/\b(middle east|north africa|menaap|mena|mna)\b/i, "Middle East, North Africa, Afghanistan, and Pakistan"],
  [/\b(south asia|sar)\b/i, "South Asia"],
  [/\b(sub-saharan africa|ssa)\b/i, "Sub-Saharan Africa"],
];

export function routeQuestion(question: string, scope: Scope, catalog: Catalog): Route {
  const detected = Object.keys(DOC_HINTS).filter((slug) => slug in catalog && DOC_HINTS[slug].test(question));
  let docs = scope !== "all" ? [scope] : detected.length ? detected : Object.keys(catalog);
  const paths: string[] = [];
  const gep = docs.includes("gep") ? catalog.gep?.sections ?? [] : [];
  const has = (path: string) => gep.some((section) => section.path === path);
  const chapter = question.match(/\bchapter\s+(\d)\b/i);
  if (chapter) {
    const found = gep.find((section) => section.level === 1 && section.path.startsWith(`Ch. ${chapter[1]} `));
    if (found) paths.push(found.path);
  }
  const box = question.match(/\bbox\s+(\d+\.\d+)\b/i);
  if (box) gep.filter((s) => s.kind === "box" && s.path.includes(`BOX ${box[1]} `)).forEach((s) => paths.push(s.path));
  if (/\bexecutive summary\b/i.test(question) && has("Executive Summary")) paths.push("Executive Summary");
  if (/\bstatistical (annex|appendix)\b/i.test(question) && has("Statistical Annex")) paths.push("Statistical Annex");
  for (const [pattern, region] of REGIONS) {
    const path = `Ch. 2 Regional Outlooks › ${region}`;
    if (pattern.test(question) && has(path)) paths.push(path);
  }
  if (paths.length && scope === "all") docs = ["gep"];
  return { docs, paths: [...new Set(paths)] };
}

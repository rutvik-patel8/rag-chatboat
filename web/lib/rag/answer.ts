import { errorMessage } from "@/lib/rag/health";
import { buildBlocks, formatSources, sectionLabel, type SourceBlock } from "@/lib/rag/context";
import { SYSTEM_PROMPT, userPrompt } from "@/lib/rag/prompt";
import { KEEP, selectRelevant } from "@/lib/rag/rerank";
import type { Candidate, SearchArgs } from "@/lib/rag/retrieve";
import { routeQuestion, scopeRoute, type Catalog, type Route, type Scope } from "@/lib/rag/router";
import { verifyAnswer } from "@/lib/rag/verify";

export type Turn = { role: "user" | "assistant"; content: string };
/** Ablation switches (spec §8.3), accepted only outside production: each turns off one technique. */
export type Options = { vectorOnly?: boolean; noRerank?: boolean; noRouter?: boolean; noFootnotes?: boolean };
export type ChatRequest = { question: string; scope: Scope; history: Turn[]; options?: Options };
export type SourceInfo = {
  id: number; slug: string; doc: string; sectionPath: string; pageStart: number; pageEnd: number; kind: string; snippet: string;
  text: string; // the whole block the model saw, for the sources drawer
};
export type Debug = {
  question: string;
  route: Route;
  candidates: { chunkId: number; vecRank: number | null; kwRank: number | null; rrf: number; rerank: number | null }[];
};
export type ChatEvent =
  | { type: "sources"; sources: SourceInfo[] }
  | { type: "text"; delta: string }
  | { type: "done"; warnings: string[]; debug: Debug }
  | { type: "error"; message: string };

export type Deps = {
  catalog(): Promise<Catalog>;
  condense(history: Turn[], question: string): Promise<string>;
  embed(question: string): Promise<number[]>;
  search(args: SearchArgs): Promise<Candidate[]>;
  rerank(question: string, candidates: Candidate[]): Promise<Map<number, number>>;
  neighbours(picked: Candidate[]): Promise<Parameters<typeof buildBlocks>[1]>;
  generate(system: string, prompt: string): AsyncIterable<string>;
};

export function refusal(scope: Scope, catalog: Catalog, candidates: Candidate[]): string {
  const where = scope === "all" ? "the documents" : catalog[scope]?.title ?? scope;
  const closest = [...new Set(candidates.map((c) => sectionLabel(c.docTitle, c.sectionPath)))].slice(0, 3);
  return `I couldn't find this in ${where}.` + (closest.length ? ` Closest sections: ${closest.join("; ")}.` : "");
}

function sourceInfo(block: SourceBlock, docTitle: string): SourceInfo {
  return {
    id: block.id, slug: block.docSlug, doc: docTitle, sectionPath: block.sectionPath, pageStart: block.pageStart,
    pageEnd: block.pageEnd, kind: block.kind, snippet: block.text.slice(0, 300), text: block.text,
  };
}

/** The whole question → answer flow (spec §6), emitting stream events. Every external call comes from `deps`. */
export async function answer(request: ChatRequest, deps: Deps, emit: (event: ChatEvent) => void): Promise<void> {
  const warnings: string[] = [];
  const question = await deps.condense(request.history, request.question);
  const catalog = await deps.catalog();
  const options = request.options ?? {};
  const route = options.noRouter ? scopeRoute(request.scope, catalog) : routeQuestion(question, request.scope, catalog);
  const vector = await deps.embed(question);
  const candidates = await deps.search({ vector, question, docs: route.docs, paths: route.paths, keyword: !options.vectorOnly });
  let scores: Map<number, number> | null = null;
  if (candidates.length && !options.noRerank) {
    try {
      scores = await deps.rerank(question, candidates);
    } catch {
      warnings.push("reranking failed; using search order");
    }
  }
  const picked = scores ? selectRelevant(candidates, scores) : candidates.slice(0, KEEP);
  const debug: Debug = {
    question, route,
    candidates: candidates.map((c) => ({ chunkId: c.id, vecRank: c.vecRank, kwRank: c.kwRank, rrf: c.score, rerank: scores?.get(c.id) ?? null })),
  };
  if (!picked.length) {
    emit({ type: "sources", sources: [] });
    emit({ type: "text", delta: refusal(request.scope, catalog, candidates) });
    emit({ type: "done", warnings, debug });
    return;
  }
  const blocks = buildBlocks(picked, await deps.neighbours(picked), undefined, { footnotes: !options.noFootnotes });
  const titles = new Map(picked.map((c) => [c.docSlug, c.docTitle]));
  emit({ type: "sources", sources: blocks.map((b) => sourceInfo(b, titles.get(b.docSlug) ?? b.docSlug)) });
  let text = "";
  try {
    for await (const delta of deps.generate(SYSTEM_PROMPT, userPrompt(question, formatSources(blocks)))) {
      text += delta;
      emit({ type: "text", delta });
    }
  } catch (error) {
    emit({ type: "error", message: `answer generation failed: ${errorMessage(error)}` });
    return;
  }
  warnings.push(...verifyAnswer(text, blocks));
  emit({ type: "done", warnings, debug });
}

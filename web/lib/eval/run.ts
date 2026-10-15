import type { Judgement } from "@/lib/eval/judge";
import type { EvalQuestion } from "@/lib/eval/questions";
import { scoreAnswer, type Outcome, type Score } from "@/lib/eval/score";
import { answer, type ChatEvent, type Deps, type Options } from "@/lib/rag/answer";
import { errorMessage } from "@/lib/rag/health";

/** A pipeline setting to evaluate: ablation switches and which model writes the answers. */
export type EvalConfig = { name: string; options: Options; model: "chat" | "utility" };

export const MAIN: EvalConfig = { name: "full", options: {}, model: "chat" };
// Ablations answer with the utility model (Flash allows ~20 answers a day), against a utility-model baseline.
export const ABLATIONS: EvalConfig[] = [
  { name: "full (lite)", options: {}, model: "utility" },
  { name: "vectorOnly", options: { vectorOnly: true }, model: "utility" },
  { name: "noRerank", options: { noRerank: true }, model: "utility" },
  { name: "noRouter", options: { noRouter: true }, model: "utility" },
  { name: "noFootnotes", options: { noFootnotes: true }, model: "utility" },
];

export type Result = {
  id: string; type: EvalQuestion["type"]; scope: EvalQuestion["scope"]; config: string; ms: number;
  answer: string; sources: { id: number; sectionPath: string; pageStart: number; pageEnd: number; kind: string }[];
  warnings: string[]; error?: string; score: Score; judge?: Judgement;
};

/** Ask one question through the real orchestrator and collect what the UI would show. */
export async function runQuestion(q: EvalQuestion, config: EvalConfig, deps: Deps): Promise<Outcome> {
  const outcome: Outcome = { answer: "", sources: [], warnings: [] };
  const emit = (event: ChatEvent) => {
    if (event.type === "sources") outcome.sources = event.sources;
    else if (event.type === "text") outcome.answer += event.delta;
    else if (event.type === "done") outcome.warnings = event.warnings;
    else outcome.error = event.message;
  };
  try {
    await answer({ question: q.question, scope: q.scope, history: [], options: config.options }, deps, emit);
  } catch (error) {
    outcome.error = errorMessage(error);
  }
  if (!outcome.error && !config.options.noRerank && outcome.warnings.some((w) => w.startsWith("reranking failed"))) {
    outcome.error = "reranking failed (the run is not the full pipeline)"; // a 429 silently dropped the relevance gate
  }
  return outcome;
}

type RunOptions = {
  judge?: (q: EvalQuestion, outcome: Outcome) => Promise<Judgement>; // rubric questions only
  onResult?: (result: Result) => void;
};

/** Every question under every config, one at a time (free-tier rate limits). */
export async function runSet(questions: EvalQuestion[], configs: EvalConfig[], depsFor: (config: EvalConfig) => Deps, options: RunOptions = {}): Promise<Result[]> {
  const results: Result[] = [];
  for (const config of configs) {
    const deps = depsFor(config);
    for (const q of questions) {
      const started = Date.now();
      const outcome = await runQuestion(q, config, deps);
      const result: Result = {
        id: q.id, type: q.type, scope: q.scope, config: config.name, ms: Date.now() - started,
        answer: outcome.answer, warnings: outcome.warnings, error: outcome.error, score: scoreAnswer(q, outcome),
        sources: outcome.sources.map(({ id, sectionPath, pageStart, pageEnd, kind }) => ({ id, sectionPath, pageStart, pageEnd, kind })),
      };
      if (options.judge && q.rubric && !outcome.error) {
        result.judge = await options.judge(q, outcome).catch(() => undefined);
      }
      results.push(result);
      options.onResult?.(result);
    }
  }
  return results;
}

/** Share query embeddings across configs: the same question is embedded once per run. */
export function shareEmbeddings(deps: Deps, cache: Map<string, Promise<number[]>>): Deps {
  return {
    ...deps,
    embed: (question) => {
      if (!cache.has(question)) cache.set(question, deps.embed(question).catch((error) => {
        cache.delete(question); // don't keep a failed call
        throw error;
      }));
      return cache.get(question)!;
    },
  };
}

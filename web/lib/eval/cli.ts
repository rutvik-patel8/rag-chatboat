import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { getEnv, utilityModel } from "@/lib/env";
import { judge } from "@/lib/eval/judge";
import { loadQuestions } from "@/lib/eval/questions";
import { ablationTable, passBar, progressLine, scorecard } from "@/lib/eval/report";
import { ABLATIONS, MAIN, runSet, shareEmbeddings, type EvalConfig } from "@/lib/eval/run";
import { getPool } from "@/lib/rag/db";
import { productionDeps } from "@/lib/rag/deps";
import { getGenAI } from "@/lib/rag/gemini";
import type { GenClient } from "@/lib/rag/genai";

export type Args = { ablate: boolean; config?: string; only?: string[] };

const USAGE = "usage: npm run eval [-- --ablate [--config <name>]] [--only <id,id>]";

export function parseArgs(argv: string[]): Args {
  const args: Args = { ablate: false };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "--ablate") args.ablate = true;
    else if (flag === "--config" && argv[i + 1]) args.config = argv[++i];
    else if (flag === "--only" && argv[i + 1]) args.only = argv[++i].split(",").map((id) => id.trim()).filter(Boolean);
    else throw new Error(`${USAGE}\nunknown or incomplete option: ${flag}`);
  }
  if (args.config && !args.ablate) throw new Error(`--config needs --ablate\n${USAGE}`);
  if (args.config && !ABLATIONS.some((c) => c.name === args.config)) {
    throw new Error(`unknown config "${args.config}"; choose one of: ${ABLATIONS.map((c) => c.name).join(", ")}`);
  }
  return args;
}

/** Run the evaluation against the live pipeline and write eval/results/<timestamp>.json. Returns the exit code. */
export async function main(argv: string[], root: string): Promise<number> {
  const args = parseArgs(argv);
  const all = loadQuestions(path.join(root, "eval", "questions.yaml"));
  const unknown = (args.only ?? []).filter((id) => !all.some((q) => q.id === id));
  if (unknown.length) throw new Error(`unknown question ids: ${unknown.join(", ")}`);
  const questions = args.only ? all.filter((q) => args.only!.includes(q.id)) : all;
  const configs: EvalConfig[] = args.ablate ? ABLATIONS.filter((c) => !args.config || c.name === args.config) : [MAIN];

  const env = getEnv();
  const helper = utilityModel(env);
  const client = getGenAI() as unknown as GenClient;
  const embeddings = new Map<string, Promise<number[]>>();
  const depsFor = (config: EvalConfig) =>
    shareEmbeddings(productionDeps(undefined, { chatModel: config.model === "utility" ? helper : env.GEMINI_CHAT_MODEL }), embeddings);

  const startedAt = new Date();
  console.log(`${questions.length} questions × ${configs.length} config(s); answers: ${configs.map((c) => `${c.name}=${c.model === "utility" ? helper : env.GEMINI_CHAT_MODEL}`).join(", ")}`);
  try {
    const results = await runSet(questions, configs, depsFor, {
      judge: args.ablate ? undefined : (q, outcome) => judge(client, helper, q, outcome.answer, outcome.sources),
      onResult: (result) => console.log(progressLine(result)),
    });
    const dir = path.join(root, "eval", "results");
    mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${startedAt.toISOString().replace(/[:.]/g, "-")}${args.ablate ? "-ablate" : ""}.json`);
    const models = { chat: env.GEMINI_CHAT_MODEL, utility: helper, embed: env.GEMINI_EMBED_MODEL };
    writeFileSync(file, JSON.stringify({ startedAt, models, configs, results }, null, 2));
    console.log(`\n${args.ablate ? ablationTable(results) : scorecard(results)}\n`);
    console.log(`results: ${path.relative(root, file)}`);
    if (args.ablate) return 0;
    if (args.only) {
      console.log("\npartial run (--only): the pass bar needs the full question set");
      return 0;
    }
    const bar = passBar(results);
    console.log(`\n${bar.lines.join("\n")}\n${bar.passed ? "PASS BAR MET" : "PASS BAR NOT MET"}`);
    return bar.passed ? 0 : 1;
  } finally {
    await getPool().end();
  }
}

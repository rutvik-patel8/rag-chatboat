// Evaluation CLI (spec §8). From web/: npm run eval [-- --ablate [--config <name>]] [--only <id,id>]
import path from "node:path";
import { loadEnvConfig } from "@next/env";
import { main } from "@/lib/eval/cli";

const root = path.resolve(__dirname, "..", ".."); // repo root: eval/questions.yaml, eval/results/, .env
loadEnvConfig(root);
main(process.argv.slice(2), root).then(
  (code) => (process.exitCode = code),
  (error: unknown) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 2;
  },
);

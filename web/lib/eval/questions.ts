import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { z } from "zod";

// One evaluation question (spec §8.1). Answerable types carry the facts the answer must state and where they live.
const PageList = z
  .array(z.union([z.number().int().positive(), z.string().regex(/^\d+-\d+$/, 'a page or a "from-to" range')]))
  .transform((items) =>
    items.flatMap((item) => {
      if (typeof item === "number") return [item];
      const [from, to] = item.split("-").map(Number);
      return Array.from({ length: to - from + 1 }, (_, i) => from + i);
    }),
  );

const Question = z
  .strictObject({
    id: z.string().regex(/^[a-z0-9-]+$/),
    type: z.enum(["brief", "coverage", "trap", "isolation"]),
    scope: z.enum(["gpo", "gep", "imac", "all"]),
    question: z.string().min(1).max(1000),
    must_contain: z.array(z.string().min(1)).default([]),
    must_not_contain: z.array(z.string().min(1)).default([]),
    expected_pages: PageList.default([]),
    expected_section: z.string().optional(),
    rubric: z.array(z.string().min(1)).optional(),
  })
  .refine((q) => q.type === "trap" || (q.must_contain.length > 0 && q.expected_pages.length > 0), {
    message: "answerable questions need must_contain and expected_pages",
  });

export type EvalQuestion = z.output<typeof Question>;

export function parseQuestions(yamlText: string): EvalQuestion[] {
  const questions = z.array(Question).parse(parse(yamlText));
  const seen = new Set<string>();
  for (const q of questions) {
    if (seen.has(q.id)) throw new Error(`duplicate question id: ${q.id}`);
    seen.add(q.id);
  }
  return questions;
}

export function loadQuestions(file: string): EvalQuestion[] {
  return parseQuestions(readFileSync(file, "utf8"));
}

import type { Result } from "@/lib/eval/run";

// Plain-text tables for the terminal (spec §8.2, §8.3).

const mark = (value: boolean | null) => (value === null ? "–" : value ? "✓" : "✗");

function table(rows: string[][]): string {
  const widths = rows[0].map((_, i) => Math.max(...rows.map((row) => row[i].length)));
  return rows.map((row) => row.map((cell, i) => cell.padEnd(widths[i])).join("  ").trimEnd()).join("\n");
}

export function scorecard(results: Result[]): string {
  const rows = results.map((r) => {
    const { hit, facts, citation, verifier, refused, isolated, pass } = r.score;
    const judged = r.judge ? `${r.judge.covered}/${r.judge.total}${r.judge.unsupported.length ? ` (${r.judge.unsupported.length} unsupported)` : ""}` : "–";
    return [r.id, r.type, mark(hit), mark(facts), citation === null ? "–" : `${Math.round(citation * 100)}%`, mark(verifier),
      mark(refused), mark(isolated), judged, pass ? "PASS" : r.error ? "ERROR" : "fail"];
  });
  return table([["question", "type", "hit", "facts", "cited", "verifier", "refused", "isolated", "rubric", "result"], ...rows]);
}

/** The pass bar of spec §1, from one config's results; any errored question also fails it. */
export function passBar(results: Result[]): { lines: string[]; passed: boolean } {
  const of = (type: string) => results.filter((r) => r.type === type);
  const passed = (rs: Result[]) => rs.filter((r) => r.score.pass).length;
  const answered = results.filter((r) => r.type !== "trap" && !r.error);
  const clean = answered.filter((r) => r.score.verifier).length;
  const checks: [string, number, number][] = [
    ["Brief questions answered with citations", passed(of("brief")), of("brief").length],
    ["Trap questions refused", passed(of("trap")), of("trap").length],
    ["Region isolation", passed(of("isolation")), of("isolation").length],
    ["Answers with every figure verified", clean, answered.length],
    ["Questions answered without errors", results.filter((r) => !r.error).length, results.length],
  ];
  return {
    lines: checks.map(([label, got, total]) => `${got === total && total > 0 ? "✓" : "✗"} ${label}: ${got}/${total}`),
    passed: checks.every(([, got, total]) => got === total && total > 0),
  };
}

const GROUPS: [string, (r: Result) => boolean][] = [
  ["GPO", (r) => r.type !== "trap" && r.scope === "gpo"],
  ["GEP", (r) => r.type !== "trap" && r.scope === "gep"],
  ["iMac", (r) => r.type !== "trap" && r.scope === "imac"],
  ["traps", (r) => r.type === "trap"],
  ["all", () => true],
];

/** Questions passed per document shape, one row per config, so each technique's contribution shows. */
export function ablationTable(results: Result[]): string {
  const configs = [...new Set(results.map((r) => r.config))];
  const rows = configs.map((config) => [
    config,
    ...GROUPS.map(([, inGroup]) => {
      const rs = results.filter((r) => r.config === config && inGroup(r));
      return rs.length ? `${rs.filter((r) => r.score.pass).length}/${rs.length}` : "–";
    }),
  ]);
  return table([["config", ...GROUPS.map(([name]) => name)], ...rows]);
}

export function progressLine(r: Result): string {
  return `${r.config.padEnd(12)} ${r.id.padEnd(22)} ${r.score.pass ? "PASS" : r.error ? `ERROR ${r.error.slice(0, 80)}` : "fail"} (${(r.ms / 1000).toFixed(1)} s)`;
}

import type { Debug } from "@/lib/rag/answer";

/** "Show retrieval": router output and each candidate's vector rank, keyword rank, RRF score and rerank score. */
export function DebugPanel({ debug }: { debug: Debug }) {
  return (
    <details open className="mt-3 rounded border border-zinc-200 p-2 text-xs dark:border-zinc-800">
      <summary className="cursor-pointer font-semibold">Retrieval</summary>
      <p className="mt-1">Searched as: {debug.question}</p>
      <p>Documents: {debug.route.docs.join(", ") || "none"}</p>
      <p>Section filter: {debug.route.paths.length ? debug.route.paths.join("; ") : "none"}</p>
      <table className="mt-2 w-full text-left tabular-nums">
        <thead>
          <tr className="text-zinc-500"><th>chunk</th><th>vector</th><th>keyword</th><th>RRF</th><th>rerank</th></tr>
        </thead>
        <tbody>
          {debug.candidates.map((c) => (
            <tr key={c.chunkId} className={c.rerank !== null && c.rerank >= 2 ? "font-semibold" : "text-zinc-500"}>
              <td>{c.chunkId}</td><td>{c.vecRank ?? "–"}</td><td>{c.kwRank ?? "–"}</td><td>{c.rrf.toFixed(4)}</td><td>{c.rerank ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

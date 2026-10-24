import type { Debug } from "@/lib/rag/answer";

/** "Show retrieval": router output and each candidate's vector rank, keyword rank, RRF score and rerank score. */
export function DebugPanel({ debug }: { debug: Debug }) {
  return (
    <details open className="mt-4 rounded-md border border-rule bg-surface px-3 py-2 text-xs text-muted">
      <summary className="cursor-pointer font-semibold text-ink">Retrieval</summary>
      <div className="mt-2 space-y-0.5">
        <p>Searched as: {debug.question}</p>
        <p>Documents: {debug.route.docs.join(", ") || "none"}</p>
        <p>Section filter: {debug.route.paths.length ? debug.route.paths.join("; ") : "none"}</p>
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left tabular-nums [&_td]:py-0.5 [&_td]:pr-3 [&_th]:pr-3 [&_th]:pb-1 [&_th]:font-medium">
          <thead>
            <tr className="border-b border-rule"><th>chunk</th><th>vector</th><th>keyword</th><th>RRF</th><th>rerank</th></tr>
          </thead>
          <tbody>
            {debug.candidates.map((c) => (
              <tr key={c.chunkId} className={c.rerank !== null && c.rerank >= 2 ? "font-semibold text-ink" : ""}>
                <td>{c.chunkId}</td><td>{c.vecRank ?? "–"}</td><td>{c.kwRank ?? "–"}</td><td>{c.rrf.toFixed(4)}</td><td>{c.rerank ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

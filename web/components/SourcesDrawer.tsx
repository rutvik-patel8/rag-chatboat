import { pageLabel, pdfHref, sourceTitle } from "@/lib/chat/format";
import type { SourceInfo } from "@/lib/rag/answer";

type Props = { sources: SourceInfo[]; active: number; onClose: () => void };

/** Every source of one answer, the clicked one first and highlighted: text, section, page, kind and a PDF link. */
export function SourcesDrawer({ sources, active, onClose }: Props) {
  const ordered = [...sources].sort((a, b) => Number(b.id === active) - Number(a.id === active));
  return (
    <aside aria-label="Sources" className="fixed inset-y-0 right-0 z-10 flex w-full max-w-md flex-col border-l border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-200 p-3 dark:border-zinc-800">
        <h2 className="font-semibold">Sources</h2>
        <button type="button" onClick={onClose} className="rounded px-2 hover:bg-zinc-100 dark:hover:bg-zinc-800" aria-label="Close sources">✕</button>
      </div>
      <ol className="flex-1 space-y-3 overflow-y-auto p-3">
        {ordered.map((s) => (
          <li key={s.id} className={`rounded border p-2 text-sm ${s.id === active ? "border-blue-500" : "border-zinc-200 dark:border-zinc-800"}`}>
            <p className="font-semibold">S{s.id} · {sourceTitle(s)}</p>
            <p className="text-xs text-zinc-500">
              {pageLabel(s)} · {s.kind} ·{" "}
              <a href={pdfHref(s)} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline dark:text-blue-300">Open page</a>
            </p>
            <p className="mt-2 whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">{s.text}</p>
          </li>
        ))}
      </ol>
    </aside>
  );
}

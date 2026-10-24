import { useEffect, useRef } from "react";
import { docStyle } from "@/components/docs";
import { pageLabel, pdfHref, sourceTitle } from "@/lib/chat/format";
import type { SourceInfo } from "@/lib/rag/answer";

type Props = { sources: SourceInfo[]; active: number; onClose: () => void };

/** Every source of one answer, the clicked one first and highlighted: text, section, page, kind and a PDF link. */
export function SourcesDrawer({ sources, active, onClose }: Props) {
  const ordered = [...sources].sort((a, b) => Number(b.id === active) - Number(a.id === active));
  const close = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    close.current?.focus();
  }, [active]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div aria-hidden onClick={onClose} className="fixed inset-0 z-20 bg-ink/20" />
      <aside aria-label="Sources" className="fixed inset-y-0 right-0 z-30 flex w-full max-w-md flex-col border-l border-rule bg-paper shadow-2xl">
        <div className="flex items-center justify-between border-b border-rule px-4 py-3">
          <h2 className="font-semibold">Sources</h2>
          <button ref={close} type="button" onClick={onClose} className="rounded-md px-2 py-0.5 text-muted hover:bg-surface hover:text-ink" aria-label="Close sources">✕</button>
        </div>
        <ol className="flex-1 space-y-3 overflow-y-auto p-4">
          {ordered.map((s) => (
            <li
              key={s.id}
              style={docStyle(s.slug)}
              className={`rounded-md border-l-4 border-(--doc) bg-surface p-3 text-sm ${s.id === active ? "ring-2 ring-(--doc)/40" : "opacity-85"}`}
            >
              <p className="font-semibold"><span className="text-(--doc)">S{s.id} · {sourceTitle(s)}</span></p>
              <p className="mt-0.5 text-xs text-muted">
                {pageLabel(s)} · {s.kind} ·{" "}
                <a href={pdfHref(s)} target="_blank" rel="noopener noreferrer" className="font-medium text-(--doc) underline underline-offset-2">Open page</a>
              </p>
              <p className="mt-2 whitespace-pre-wrap font-serif text-[0.975rem] leading-relaxed">{s.text}</p>
            </li>
          ))}
        </ol>
      </aside>
    </>
  );
}

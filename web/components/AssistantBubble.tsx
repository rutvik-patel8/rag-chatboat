import { AnswerText } from "@/components/AnswerText";
import { DebugPanel } from "@/components/DebugPanel";
import { pageLabel, sourceTitle } from "@/lib/chat/format";
import type { AssistantMessage } from "@/lib/chat/thread";

type Props = { message: AssistantMessage; showDebug: boolean; onCite: (id: number) => void };

export function AssistantBubble({ message, showDebug, onCite }: Props) {
  const { status, text, sources, warnings, debug, error } = message;
  return (
    <div className="rounded-lg bg-zinc-100 p-3 dark:bg-zinc-900">
      {text ? (
        <AnswerText text={text} sourceIds={sources.map((s) => s.id)} onCite={onCite} />
      ) : status === "streaming" ? (
        <p className="text-zinc-500">{sources.length ? "Writing the answer…" : "Searching the documents…"}</p>
      ) : null}
      {status === "stopped" && <p className="mt-2 text-sm text-zinc-500">Stopped.</p>}
      {error && <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}
      {warnings.length > 0 && (
        <p className="mt-2 inline-block rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200" title={warnings.join("\n")}>
          ⚠ {warnings.map((w) => w[0].toUpperCase() + w.slice(1)).join("; ")}
        </p>
      )}
      {sources.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-1 text-xs">
          {sources.map((s) => (
            <li key={s.id}>
              <button type="button" onClick={() => onCite(s.id)} className="rounded border border-zinc-300 px-1.5 py-0.5 text-left hover:bg-white dark:border-zinc-700 dark:hover:bg-zinc-800">
                <b>S{s.id}</b> {sourceTitle(s)} · {pageLabel(s)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {showDebug && debug && <DebugPanel debug={debug} />}
    </div>
  );
}

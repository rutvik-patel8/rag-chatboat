import { useState } from "react";
import { AnswerText } from "@/components/AnswerText";
import { DebugPanel } from "@/components/DebugPanel";
import { docStyle } from "@/components/docs";
import { pageLabel, sourceTitle } from "@/lib/chat/format";
import type { AssistantMessage } from "@/lib/chat/thread";

type Props = { message: AssistantMessage; showDebug: boolean; onCite: (id: number) => void };

export function AssistantBubble({ message, showDebug, onCite }: Props) {
  const { status, text, sources, warnings, debug, error } = message;
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="pb-2">
      {text ? (
        <AnswerText text={text} sources={sources} onCite={onCite} />
      ) : status === "streaming" ? (
        <div>
          <p className="text-sm text-muted" aria-live="polite">{sources.length ? "Writing the answer…" : "Searching the documents…"}</p>
          <div aria-hidden className="mt-3 space-y-2.5 motion-safe:animate-pulse">
            {["w-full", "w-11/12", "w-2/3"].map((w) => <div key={w} className={`h-3 rounded-sm bg-rule ${w}`} />)}
          </div>
        </div>
      ) : null}
      {status === "stopped" && <p className="mt-3 text-sm text-muted">Stopped.</p>}
      {error && <p role="alert" className="mt-3 rounded-md bg-error-bg px-3 py-2 text-sm text-error-ink">{error}</p>}
      {(warnings.length > 0 || (status === "done" && text)) && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {status === "done" && text && (
            <button type="button" onClick={copy} className="-ml-1.5 rounded-md px-1.5 py-0.5 text-xs font-medium text-muted hover:bg-surface hover:text-ink">
              {copied ? "Copied" : "Copy answer"}
            </button>
          )}
          {warnings.length > 0 && (
            <p className="rounded-md bg-warn-bg px-2 py-1 text-xs text-warn-ink" title={warnings.join("\n")}>
              ⚠ {warnings.map((w) => w[0].toUpperCase() + w.slice(1)).join("; ")}
            </p>
          )}
        </div>
      )}
      {sources.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold text-muted">Sources</h3>
          <ul className="mt-1.5 space-y-px text-sm">
            {sources.map((s) => (
              <li key={s.id} style={docStyle(s.slug)}>
                <button
                  type="button"
                  onClick={() => onCite(s.id)}
                  className="flex w-full gap-2 rounded-r-md border-l-2 border-(--doc) py-1 pr-2 pl-2.5 text-left hover:bg-(--doc)/8"
                >
                  <b className="shrink-0 text-(--doc)">S{s.id}</b>
                  <span className="min-w-0 text-ink/80">{sourceTitle(s)} · {pageLabel(s)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {showDebug && debug && <DebugPanel debug={debug} />}
    </div>
  );
}

import { parseAnswer, type Inline } from "@/lib/chat/format";

type Props = { text: string; sourceIds: number[]; onCite: (id: number) => void };

function Parts({ parts, onCite }: { parts: Inline[]; onCite: (id: number) => void }) {
  return parts.map((part, i) =>
    part.type === "cite" ? (
      <button
        key={i}
        type="button"
        onClick={() => onCite(part.id)}
        className="mx-0.5 rounded bg-blue-100 px-1 align-baseline text-xs font-semibold text-blue-800 hover:bg-blue-200 dark:bg-blue-950 dark:text-blue-200"
      >
        S{part.id}
      </button>
    ) : part.strong ? (
      <strong key={i}>{part.text}</strong>
    ) : (
      <span key={i}>{part.text}</span>
    ),
  );
}

/** The answer's paragraphs and list items, with [S#] citations as clickable chips. */
export function AnswerText({ text, sourceIds, onCite }: Props) {
  return (
    <div className="space-y-2 leading-relaxed">
      {parseAnswer(text, new Set(sourceIds)).map((block, i) =>
        block.type === "li" ? (
          <div key={i} className="flex gap-2 pl-2">
            <span className="shrink-0 text-zinc-500">{block.marker}</span>
            <span><Parts parts={block.parts} onCite={onCite} /></span>
          </div>
        ) : (
          <p key={i}><Parts parts={block.parts} onCite={onCite} /></p>
        ),
      )}
    </div>
  );
}

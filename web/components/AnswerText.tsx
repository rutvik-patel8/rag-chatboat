import { docStyle } from "@/components/docs";
import { parseAnswer, type Inline } from "@/lib/chat/format";
import type { SourceInfo } from "@/lib/rag/answer";

type Source = Pick<SourceInfo, "id" | "slug">;
type Props = { text: string; sources: Source[]; onCite: (id: number) => void };

function Parts({ parts, slugs, onCite }: { parts: Inline[]; slugs: Map<number, string>; onCite: (id: number) => void }) {
  return parts.map((part, i) =>
    part.type === "cite" ? (
      <button
        key={i}
        type="button"
        onClick={() => onCite(part.id)}
        style={docStyle(slugs.get(part.id) ?? "")}
        className="mx-0.5 rounded-sm bg-(--doc)/12 px-1 py-px align-[0.1em] font-sans text-[0.7rem] font-semibold text-(--doc) hover:bg-(--doc)/22"
      >
        S{part.id}
      </button>
    ) : part.strong ? (
      <strong key={i} className="font-semibold">{part.text}</strong>
    ) : (
      <span key={i}>{part.text}</span>
    ),
  );
}

/** The answer's paragraphs and list items, with [S#] citations as chips in their document's colour. */
export function AnswerText({ text, sources, onCite }: Props) {
  const slugs = new Map(sources.map((s) => [s.id, s.slug]));
  return (
    <div className="space-y-3 font-serif text-[1.075rem] leading-[1.7]">
      {parseAnswer(text, new Set(slugs.keys())).map((block, i) =>
        block.type === "li" ? (
          <div key={i} className="flex gap-2.5 pl-1 [&:has(+div)]:mb-1.5">
            <span className="w-4 shrink-0 text-right text-muted">{block.marker}</span>
            <span><Parts parts={block.parts} slugs={slugs} onCite={onCite} /></span>
          </div>
        ) : (
          <p key={i}><Parts parts={block.parts} slugs={slugs} onCite={onCite} /></p>
        ),
      )}
    </div>
  );
}

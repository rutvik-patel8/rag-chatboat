import type { CSSProperties } from "react";
import type { Scope } from "@/lib/rag/router";

export const DOCS: Record<Exclude<Scope, "all">, { title: string; about: string }> = {
  gpo: {
    title: "GPO Annual Report 2018",
    about: "The U.S. Government Publishing Office: depository libraries, govinfo, printing and finances.",
  },
  gep: {
    title: "Global Economic Prospects, June 2026",
    about: "The World Bank's growth forecasts, regional outlooks and risks.",
  },
  imac: {
    title: "iMac Product Environmental Report",
    about: "The iMac's carbon footprint, energy use and materials (October 2024).",
  },
};

/** Sets --doc to the document's colour, for classes like text-(--doc); unknown slugs fall back to ink. */
export function docStyle(slug: string): CSSProperties {
  return { "--doc": `var(--doc-${slug}, var(--ink))` } as CSSProperties;
}

/** A small swatch in the document's colour; "all" shows the three reports stacked. */
export function DocMark({ scope }: { scope: string }) {
  if (scope === "all") {
    return (
      <span aria-hidden className="inline-flex h-3 w-2.5 shrink-0 flex-col gap-px">
        {Object.keys(DOCS).map((slug) => (
          <span key={slug} style={docStyle(slug)} className="flex-1 rounded-[1px] bg-(--doc)" />
        ))}
      </span>
    );
  }
  return <span aria-hidden style={docStyle(scope)} className="inline-block h-3 w-2.5 shrink-0 rounded-[2px] bg-(--doc)" />;
}

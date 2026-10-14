"""Per-document ingestion summary printed at the end of a run (spec §5.1 step 8)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Report:
    slug: str
    title: str
    pages: int = 0
    sections: int = 0
    notes: int = 0
    boilerplate: int = 0
    chunks_by_kind: Counter = field(default_factory=Counter)
    unresolved: list[tuple[int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def format(self) -> str:
        kinds = ", ".join(f"{kind} {n}" for kind, n in sorted(self.chunks_by_kind.items()))
        total = sum(self.chunks_by_kind.values())
        unresolved = ", ".join(f"p{page}:{n}" for page, n in self.unresolved) or "none"
        lines = [
            f"{self.slug} — {self.title}",
            f"  pages {self.pages} · sections {self.sections} · notes {self.notes} · boilerplate patterns {self.boilerplate}",
            f"  chunks: {kinds} (total {total})",
            f"  unresolved footnote markers: {unresolved}",
        ]
        lines += [f"  warning: {w}" for w in self.warnings]
        return "\n".join(lines)

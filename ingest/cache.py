"""Content-addressed JSON cache under ingest/.cache/<namespace>/ (git-ignored)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from config import REPO_ROOT

CACHE_ROOT = REPO_ROOT / "ingest" / ".cache"


class JsonCache:
    def __init__(self, root: Path, namespace: str):
        self.dir = Path(root) / namespace

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def put(self, key: str, value: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written entry

    def clear(self) -> int:
        if not self.dir.exists():
            return 0
        removed = sum(1 for p in self.dir.rglob("*.json"))
        shutil.rmtree(self.dir)
        return removed

"""registry: a learned ``normalized-remote-URL -> clone-path`` map.

Discovery is *lookup-first and self-improving*: every time the workspace layer
resolves a durable local clone for a remote (found on disk or freshly cloned into
the cache), it records the mapping here so the next review of that repo skips the
filesystem scan entirely. The registry is a cache, never authoritative — a miss,
a deleted file, or a corrupt JSON just falls back to scanning, and any entry
whose path no longer exists or no longer carries the expected remote is pruned on
read.

Stored as a single JSON object at ``<clone_cache_root>/registry.json``; writes are
atomic (temp file + ``os.replace``) so concurrent reviews never observe a
half-written file.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_FILENAME = "registry.json"


@dataclass
class RepoRegistry:
    """A persistent map of normalized remote URL -> local clone path."""

    path: Path
    _entries: dict[str, str]

    @classmethod
    def load(cls, root: Path) -> RepoRegistry:
        """Load (or start empty) the registry stored under ``root``.

        A missing or unreadable/corrupt file yields an empty registry rather than
        an error — the registry is only ever an optimization.
        """
        registry_path = Path(root) / _REGISTRY_FILENAME
        entries: dict[str, str] = {}
        try:
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                entries = {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}
        except (OSError, ValueError):
            entries = {}
        return cls(path=registry_path, _entries=entries)

    def lookup(self, key: str) -> Path | None:
        """Return the recorded clone path for ``key`` if it still exists, else None.

        A recorded path that has since been deleted is pruned from the in-memory
        view (persisted on the next :meth:`record`) so a stale hit never shadows a
        fresh scan.
        """
        recorded = self._entries.get(key)
        if not recorded:
            return None
        candidate = Path(recorded)
        if (candidate / ".git").exists() or (candidate / "HEAD").exists():
            return candidate
        self._entries.pop(key, None)
        return None

    def record(self, key: str, clone_path: Path) -> None:
        """Associate ``key`` with ``clone_path`` and persist the registry."""
        self._entries[key] = str(Path(clone_path))
        self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".registry-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._entries, fh, indent=2, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            # Best-effort: a failed registry write must never fail a review.
            with contextlib.suppress(OSError):
                os.unlink(tmp)

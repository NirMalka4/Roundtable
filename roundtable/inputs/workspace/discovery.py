"""discovery: find a local clone of a reviewed repo by *remote identity*.

Never match by folder name — two unrelated folders can share a name and one repo
can live under many. A candidate matches only when its ``remote.origin.url``
normalizes (:func:`normalize_remote_url`) to the same key as the reviewed repo.

Resolution order (first hit wins), each cheaper than the next:

1. **Learned registry** — a persisted ``key -> clone_path`` map (lookup-first,
   self-improving). Skips the scan entirely on a repeat review.
2. **Configured search paths** — a bounded-depth ``.git`` scan under
   ``repo_search_paths`` (default ``[~/repos]``, one entry, not an assumption
   baked elsewhere).
3. **Contextual defaults** — the parent of the invoking repo / cwd, for the common
   case where the target sits beside where Roundtable was launched.

Ambiguity is handled explicitly: candidates are de-duplicated by git-common-dir
(so a repo and its own worktrees count once); if more than one *distinct* clone
still matches, discovery makes a deterministic choice (lexicographically smallest
path) and records every candidate plus the selection reason — it never silently
takes an arbitrary first match.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .._gitexec import run_git
from .registry import RepoRegistry
from .remote_url import normalize_remote_url

#: Directory names never worth descending into during a clone scan.
_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "bin", "obj", "dist", "build"}
)

_DEFAULT_SEARCH_PATHS = (str(Path.home() / "repos"),)


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of a discovery attempt.

    ``path`` is ``None`` on a miss (the caller then clones). ``candidates`` lists
    every distinct clone that matched (usually 0 or 1; >1 means the ambiguity
    path fired) and ``reason`` explains the selection for the observability trail.
    """

    remote_key: str | None
    path: Path | None
    source: str  # 'registry' | 'search_path' | 'contextual' | 'miss'
    candidates: tuple[str, ...]
    reason: str


def _remote_key_of(repo_root: str) -> str | None:
    """Normalized remote key of the clone at ``repo_root``, or ``None``."""
    try:
        url = run_git(
            ["config", "--get", "remote.origin.url"], repo_root, timeout=5.0
        ).stdout.strip()
    except Exception:
        return None
    return normalize_remote_url(url) if url else None


def _git_common_dir(repo_root: str) -> str | None:
    """Absolute git-common-dir of ``repo_root`` (shared by a repo + its worktrees)."""
    try:
        out = run_git(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"], repo_root, timeout=5.0
        ).stdout.strip()
    except Exception:
        return None
    return os.path.normcase(os.path.abspath(out)) if out else None


def _scan_for_clones(root: str, target_key: str, max_depth: int) -> list[str]:
    """Return repo roots under ``root`` (bounded depth) whose remote matches.

    A directory containing ``.git`` is treated as a repo boundary: it is tested
    and not descended into. Heavy/uninteresting directories are skipped.
    """
    matches: list[str] = []
    base = Path(root).expanduser()
    if not base.is_dir():
        return matches

    base_str = str(base)
    base_depth = base_str.rstrip(os.sep).count(os.sep)
    for current, dirnames, _files in os.walk(base_str):
        depth = current.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        if os.path.exists(os.path.join(current, ".git")):
            if _remote_key_of(current) == target_key:
                matches.append(current)
            dirnames[:] = []  # a repo boundary — never descend into a checkout
            continue
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
    return matches


def _dedup_by_common_dir(paths: Sequence[str]) -> list[str]:
    """Collapse paths that share a git-common-dir (a repo and its worktrees)."""
    seen: dict[str, str] = {}
    for path in paths:
        common = _git_common_dir(path) or os.path.normcase(os.path.abspath(path))
        # Keep the lexicographically smallest representative per clone identity.
        if common not in seen or path < seen[common]:
            seen[common] = path
    return sorted(seen.values())


def discover_clone(
    remote_url: str,
    *,
    search_paths: Sequence[str] = (),
    contextual_dirs: Sequence[str] = (),
    registry: RepoRegistry | None = None,
    max_depth: int = 3,
) -> DiscoveryResult:
    """Discover a local clone for ``remote_url`` (see module docstring for order)."""
    target_key = normalize_remote_url(remote_url)
    if target_key is None:
        return DiscoveryResult(None, None, "miss", (), f"unparseable remote: {remote_url!r}")

    # 1. Learned registry.
    if registry is not None:
        hit = registry.lookup(target_key)
        if hit is not None and _remote_key_of(str(hit)) == target_key:
            return DiscoveryResult(
                target_key, hit, "registry", (str(hit),), "registry hit (verified remote)"
            )

    roots = tuple(search_paths) or _DEFAULT_SEARCH_PATHS

    # 2 + 3. Scan configured search paths, then contextual defaults.
    for source, dirs in (("search_path", roots), ("contextual", tuple(contextual_dirs))):
        raw_matches: list[str] = []
        for directory in dirs:
            raw_matches.extend(_scan_for_clones(directory, target_key, max_depth))
        distinct = _dedup_by_common_dir(raw_matches)
        if not distinct:
            continue
        chosen = Path(distinct[0])
        if registry is not None:
            registry.record(target_key, chosen)
        reason = (
            f"single match under {source}"
            if len(distinct) == 1
            else f"{len(distinct)} clones matched under {source}; chose smallest path deterministically"
        )
        return DiscoveryResult(target_key, chosen, source, tuple(distinct), reason)

    return DiscoveryResult(target_key, None, "miss", (), "no local clone matched remote")

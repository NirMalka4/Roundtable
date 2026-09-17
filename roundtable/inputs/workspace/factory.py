"""factory: build a :class:`ReviewWorkspace` from settings — the CLI's one call.

Encapsulates the plumbing the CLI should not own: turning
:class:`~roundtable.settings.workspace.WorkspaceSettings` into a concrete
:class:`CloneCache` + :class:`RepoRegistry` + resolved search paths, then
delegating to :func:`resolve_workspace`. Keeping this here (not in ``cli.py``)
preserves the WHERE-seam boundary: the CLI passes a request + settings and gets
back a resolved directory, never touching cache/registry internals.

Defaults (applied only when a setting is unset):

* clone cache root — ``~/roundtable/clones`` (a *sibling* of the artifacts
  tree, deliberately not under it, so a per-session artifacts sweep never
  deletes a durable clone).
* repo search paths — ``[~/repos]`` (one unbiased default; override via
  ``workspace.repo_search_paths`` / ``ROUNDTABLE_REPO_SEARCH_PATHS``).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from roundtable.bundle import home_root

from .clone_cache import CloneCache
from .registry import RepoRegistry
from .resolver import ReviewWorkspace, WorkspaceRequest, resolve_workspace

if TYPE_CHECKING:
    from roundtable.settings import WorkspaceSettings

DEFAULT_CLONE_CACHE_DIR = home_root() / "clones"
DEFAULT_SEARCH_PATHS: tuple[str, ...] = (str(Path.home() / "repos"),)


def clone_cache_root(settings: WorkspaceSettings) -> Path:
    """The clone-cache root: the configured dir, else the built-in default."""
    if settings.clone_cache_dir:
        return Path(settings.clone_cache_dir).expanduser()
    return DEFAULT_CLONE_CACHE_DIR


def search_paths(settings: WorkspaceSettings) -> tuple[str, ...]:
    """Configured repo-search roots, else the single unbiased ``~/repos`` default."""
    return tuple(settings.repo_search_paths) or DEFAULT_SEARCH_PATHS


def build_clone_cache(settings: WorkspaceSettings) -> CloneCache:
    root = clone_cache_root(settings)
    return CloneCache(
        root=root,
        max_gb=float(settings.clone_cache_max_gb),
        ttl_days=float(settings.clone_cache_ttl_days),
    )


def reconcile_orphans(settings: WorkspaceSettings) -> list[Path]:
    """Best-effort reclaim of clones left behind by a crashed prior review.

    Runs the cache's lease-aware GC, which reaps expired leases, prunes orphaned
    worktree registrations (a per-session worktree whose directory vanished on
    crash), and collects clones past TTL / over budget — while never touching a
    clone a live review still leases or shares a worktree with. Safe to call at
    startup; any failure is swallowed so it can never fail a review.
    """
    try:
        return build_clone_cache(settings).gc()
    except Exception:
        return []


def build_review_workspace(
    request: WorkspaceRequest,
    *,
    settings: WorkspaceSettings,
    worktree_dir: Path,
    contextual_dirs: Sequence[str] = (),
) -> ReviewWorkspace:
    """Resolve ``request`` into a :class:`ReviewWorkspace` using ``settings``.

    ``worktree_dir`` is the per-session ephemeral directory where a detached
    worktree is materialized for ``--pr`` / URL reviews (unused for a local,
    in-place review). ``contextual_dirs`` are extra discovery hints (e.g. the
    invocation cwd) tried after the registry + configured search paths.
    """
    cache = build_clone_cache(settings)
    registry = RepoRegistry.load(cache.root)
    return resolve_workspace(
        request,
        cache=cache,
        worktree_dir=worktree_dir,
        registry=registry,
        search_paths=search_paths(settings),
        contextual_dirs=contextual_dirs,
        checkout_timeout_seconds=settings.checkout_timeout_seconds,
    )

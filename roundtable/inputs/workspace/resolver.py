"""resolver: the ReviewWorkspace façade — one entry point for every mode.

Given a :class:`WorkspaceRequest`, resolve a directory whose contents match the
reviewed revision and a cleanup handle, hiding the local-vs-clone and
in-place-vs-worktree decisions behind one call:

* **live-checkout** — a local branch review at the working tree's committed HEAD.
  Reviewed in place (``repo_path`` as-is): a worktree would drop a dirty working
  tree, and local review already targets committed HEAD.
* **local-worktree** — a ``--pr`` / URL whose repo is already cloned on disk
  (found by remote identity). A detached worktree at the source SHA is added over
  that clone, so the agents read exactly the reviewed revision without disturbing
  the user's checkout.
* **clone-worktree** — a ``--pr`` / URL whose repo isn't on disk. It's cloned into
  the bounded cache (and leased for the review's lifetime), then a detached
  worktree at the source SHA is added over the cache clone.

The returned :class:`ReviewWorkspace.mode` is the *authoritative* mode — callers
no longer guess it from path shape.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .clone_cache import CloneCache
from .discovery import DiscoveryResult, discover_clone
from .registry import RepoRegistry
from .remote_url import normalize_remote_url
from .worktree import add_detached_worktree, link_node_modules

MODE_LIVE_CHECKOUT = "live-checkout"
MODE_LOCAL_WORKTREE = "local-worktree"
MODE_CLONE_WORKTREE = "clone-worktree"


class WorkspaceError(RuntimeError):
    """Raised when a review workspace cannot be resolved."""


@dataclass(frozen=True)
class WorkspaceRequest:
    """What to resolve. ``mode`` is the *review* mode, not the workspace mode.

    * ``mode == 'local'`` → review the working tree in place (needs ``repo_path``).
    * ``mode == 'pr'`` → check out ``source_sha`` (needs ``remote_url`` +
      ``source_sha``); ``repo_path`` is an optional discovery hint.
    """

    mode: str  # 'local' | 'pr'
    repo_path: str | None = None
    remote_url: str | None = None
    source_sha: str | None = None
    base_sha: str | None = None
    target_ref: str | None = None


@dataclass
class ReviewWorkspace:
    """A resolved review directory plus a best-effort, idempotent cleanup."""

    path: Path
    mode: str  # MODE_LIVE_CHECKOUT | MODE_LOCAL_WORKTREE | MODE_CLONE_WORKTREE
    source_sha: str | None
    base_sha: str | None
    discovery: DiscoveryResult | None = None
    clone_path: Path | None = None
    _cleanups: list[Callable[[], None]] = field(default_factory=list)
    _done: bool = False

    def cleanup(self) -> None:
        """Run registered teardown steps in reverse (worktree before lease)."""
        if self._done:
            return
        self._done = True
        for step in reversed(self._cleanups):
            # teardown is best-effort — never fail a review on cleanup
            with contextlib.suppress(Exception):
                step()

    def __enter__(self) -> ReviewWorkspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()


def resolve_workspace(
    request: WorkspaceRequest,
    *,
    cache: CloneCache,
    worktree_dir: Path,
    registry: RepoRegistry | None = None,
    search_paths: Sequence[str] = (),
    contextual_dirs: Sequence[str] = (),
    checkout_timeout_seconds: float = 600.0,
) -> ReviewWorkspace:
    """Resolve ``request`` into a :class:`ReviewWorkspace` (see module docstring).

    ``worktree_dir`` is where a detached worktree is materialized (per-session,
    ephemeral) for the ``--pr`` / URL modes; it is unused for a local review.
    """
    if request.mode == "local":
        if not request.repo_path:
            raise WorkspaceError("local review requires repo_path")
        return ReviewWorkspace(
            path=Path(request.repo_path),
            mode=MODE_LIVE_CHECKOUT,
            source_sha=request.source_sha,
            base_sha=request.base_sha,
        )

    if request.mode != "pr":
        raise WorkspaceError(f"unknown review mode: {request.mode!r}")
    if not request.remote_url or not request.source_sha:
        raise WorkspaceError("pr review requires remote_url and source_sha")

    disc = discover_clone(
        request.remote_url,
        search_paths=search_paths,
        contextual_dirs=contextual_dirs,
        registry=registry,
    )

    cleanups: list[Callable[[], None]] = []
    if disc.path is not None:
        clone_path = disc.path
        workspace_mode = MODE_LOCAL_WORKTREE
    else:
        clone_path = cache.ensure(request.remote_url, source_sha=request.source_sha)
        lease = cache.lease(clone_path)
        cleanups.append(lease.release)  # released AFTER the worktree is removed
        if registry is not None:
            key = normalize_remote_url(request.remote_url)
            if key:
                registry.record(key, clone_path)
        workspace_mode = MODE_CLONE_WORKTREE

    handle = add_detached_worktree(
        clone_path,
        request.source_sha,
        worktree_dir,
        timeout_seconds=checkout_timeout_seconds,
    )
    cleanups.append(handle.cleanup)  # runs first (reverse order): worktree, then lease

    # Convenience: let the worktree reuse the clone's installed node_modules when the
    # reviewed revision's lockfile is unchanged. Best-effort — never fails the review.
    link_node_modules(
        clone_path, handle.path, base_sha=request.base_sha, source_sha=request.source_sha
    )

    return ReviewWorkspace(
        path=handle.path,
        mode=workspace_mode,
        source_sha=request.source_sha,
        base_sha=request.base_sha,
        discovery=disc,
        clone_path=clone_path,
        _cleanups=cleanups,
    )

"""workspace: resolve a filesystem path guaranteed to be at the review SHA.

The **WHERE** seam of the inputs subsystem. Given a review request (local branch,
``--pr`` id, or ADO PR URL) it resolves a :class:`ReviewWorkspace` — a directory
the review agents can read via ``--add-dir`` / ``cwd`` whose content matches the
diff — and a cleanup handle. It unifies two historically divergent code paths
(in-place local review vs. the stale-``--add-dir`` ``--pr`` path) behind one
façade:

* discover a local clone by *remote identity* (never folder name);
* on a miss, materialize one in a bounded, GC'd blobless clone cache;
* for ``--pr`` / URL, check out the source SHA in a detached worktree so the
  files agents read are exactly the reviewed revision (fixes the staleness trap);
* for a local branch already at the review SHA, review **in place** so a dirty
  working tree is preserved.

See ``inputs/README.md`` for the full three-seam design and invariants.
"""

from __future__ import annotations

from .clone_cache import CloneCache, CloneError, repo_key
from .discovery import DiscoveryResult, discover_clone
from .factory import (
    DEFAULT_CLONE_CACHE_DIR,
    DEFAULT_SEARCH_PATHS,
    build_clone_cache,
    build_review_workspace,
    clone_cache_root,
    reconcile_orphans,
    search_paths,
)
from .locking import Lease, acquire_lease, has_active_lease
from .registry import RepoRegistry
from .remote_url import canonical_ado_identity, normalize_remote_url
from .resolver import (
    MODE_CLONE_WORKTREE,
    MODE_LIVE_CHECKOUT,
    MODE_LOCAL_WORKTREE,
    ReviewWorkspace,
    WorkspaceError,
    WorkspaceRequest,
    resolve_workspace,
)
from .worktree import (
    WorktreeError,
    WorktreeHandle,
    add_detached_worktree,
    link_node_modules,
)

__all__ = [
    "DEFAULT_CLONE_CACHE_DIR",
    "DEFAULT_SEARCH_PATHS",
    "MODE_CLONE_WORKTREE",
    "MODE_LIVE_CHECKOUT",
    "MODE_LOCAL_WORKTREE",
    "CloneCache",
    "CloneError",
    "DiscoveryResult",
    "Lease",
    "RepoRegistry",
    "ReviewWorkspace",
    "WorkspaceError",
    "WorkspaceRequest",
    "WorktreeError",
    "WorktreeHandle",
    "acquire_lease",
    "add_detached_worktree",
    "build_clone_cache",
    "build_review_workspace",
    "canonical_ado_identity",
    "clone_cache_root",
    "discover_clone",
    "has_active_lease",
    "link_node_modules",
    "normalize_remote_url",
    "reconcile_orphans",
    "repo_key",
    "resolve_workspace",
    "search_paths",
]

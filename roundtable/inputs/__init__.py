"""inputs: Phase 1 — review-input construction (git diff context + PR binding).

The deterministic git input subsystem:

  * ``repo_detector``  — repo name / branch / remote from git.
  * ``pr_reference``   — parse the ``--pr`` argument + ADO remote URL.
  * ``git_context``    — the unified diff gatherer that
                         produces the oracle-compatible git-context string,
                         changed-file list, per-file history, and SHA-pinned
                         metadata: ``snapshot_sha`` / ``base_branch`` / ``base_sha``).
  * ``pr_diff``        — PR metadata (ADO REST, NOT the MCP agent): title,
                         branches, and the permanent merge-commit SHAs the review
                         workspace is materialized from.

Both local and ``--pr`` / URL reviews now flow through the single ``git_context``
three-dot gatherer over a workspace at the reviewed revision; the old REST/SHA-range
diff construction has been retired.

Reduction #5: publish-time hunk resolution comes from the LOCAL review
worktree SHA range only; bare-cache resolution and iteration anchoring are
DEFERRED. Note: publishing does NOT replay ``git diff`` in the review worktree —
``ado.publish_flow.run_publish`` anchors comments purely from persisted
``changedFiles`` + ADO iteration APIs (iteration ordinal + per-file
``changeTrackingId``), so it needs no local checkout at publish time. The persisted
``base_sha`` / ``snapshot_sha`` metadata is diff-provenance (it pins the exact
``git diff base_sha...snapshot_sha`` a human can reproduce), not a publish input.
"""

from __future__ import annotations

from ._gitexec import GitError, GitResult, run_git
from .ado_identity import (
    AdoIdentity,
    build_identity,
    resolve_ado_identities,
)
from .git_context import (
    ContextMetadata,
    Mode,
    ReviewContextOptions,
    ReviewContextResult,
    determine_base_branch,
    extract_changed_files,
    gather_review_context,
    resolve_default_base_branch,
    resolve_target_ref,
)
from .pr_diff import (
    PrMetadata,
    fetch_pr_by_merge_commit,
    fetch_pr_metadata,
)
from .pr_reference import (
    ParsedAdoUrl,
    PrReference,
    ado_clone_url,
    parse_ado_remote_url,
    parse_pr_reference,
    parse_pr_url,
)
from .replay import (
    REPLAY_CONTEXT_FILE,
    ReplayContext,
    ReplayError,
    ReplaySession,
    capture_replay_context,
    load_replay_session,
    materialize_replay_access,
    restore_replay_session,
)
from .repo_detector import RepoInfo, detect_repos
from .workspace import (
    WorkspaceError,
    WorkspaceRequest,
    build_review_workspace,
    reconcile_orphans,
)

__all__ = [
    "REPLAY_CONTEXT_FILE",
    "AdoIdentity",
    "ContextMetadata",
    "GitError",
    "GitResult",
    "Mode",
    "ParsedAdoUrl",
    "PrMetadata",
    "PrReference",
    "ReplayContext",
    "ReplayError",
    "ReplaySession",
    "RepoInfo",
    "ReviewContextOptions",
    "ReviewContextResult",
    "WorkspaceError",
    "WorkspaceRequest",
    "ado_clone_url",
    "build_identity",
    "build_review_workspace",
    "capture_replay_context",
    "detect_repos",
    "determine_base_branch",
    "extract_changed_files",
    "fetch_pr_by_merge_commit",
    "fetch_pr_metadata",
    "gather_review_context",
    "load_replay_session",
    "materialize_replay_access",
    "parse_ado_remote_url",
    "parse_pr_reference",
    "parse_pr_url",
    "reconcile_orphans",
    "resolve_ado_identities",
    "resolve_default_base_branch",
    "resolve_target_ref",
    "restore_replay_session",
    "run_git",
]

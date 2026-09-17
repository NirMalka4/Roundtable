"""repo_detector: detect the active git repository at a working directory.

Detects the git repository at a working directory: canonical repo name (worktree
aware), current branch, and remote origin URL. CLI operates on one repo at a time,
so :func:`detect_repos` returns a single-element list (or empty when ``cwd`` is not
inside a git repo).

Canonical-name resolution (TRAP-43): for a *linked
worktree* whose basename is a session id, ``git rev-parse --git-common-dir`` points
at the MAIN repo's ``.git`` so the canonical name is the parent folder's basename —
not the worktree basename.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ._gitexec import run_git


@dataclass(frozen=True)
class RepoInfo:
    """Information about a detected git repository."""

    name: str
    root_path: str
    branch: str | None = None
    remote_url: str | None = None


def detect_repos(cwd: str) -> list[RepoInfo]:
    """Detect the git repo at ``cwd``.

    Returns ``[RepoInfo]`` or ``[]`` when ``cwd`` is not inside a git repository.
    """
    try:
        root = run_git(["rev-parse", "--show-toplevel"], cwd, timeout=5.0).stdout.strip()
    except Exception:
        return []
    if not root:
        return []

    name = os.path.basename(root)
    try:
        common_dir = run_git(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            root,
            timeout=5.0,
        ).stdout.strip()
        if common_dir:
            # dirname strips the trailing ``.git`` segment -> canonical repo root.
            canonical_root = os.path.dirname(common_dir)
            canonical_name = os.path.basename(canonical_root)
            if canonical_name:
                name = canonical_name
    except Exception:
        # Older git or unusual layout — keep basename(root).
        pass

    branch: str | None = None
    try:
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root, timeout=5.0).stdout.strip()
        if branch == "HEAD":
            branch = None  # detached HEAD
    except Exception:
        branch = None

    remote_url: str | None = None
    try:
        remote_url = (
            run_git(["remote", "get-url", "origin"], root, timeout=5.0).stdout.strip() or None
        )
    except Exception:
        remote_url = None

    return [RepoInfo(name=name, root_path=root, branch=branch, remote_url=remote_url)]

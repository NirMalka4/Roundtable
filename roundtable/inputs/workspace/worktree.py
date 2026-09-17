"""worktree: create and tear down a detached review worktree over a clone.

For ``--pr`` / URL reviews the reviewed revision is *not* the clone's current
checkout, so we materialize the exact source SHA in a linked, detached worktree
(``git worktree add --detach <sha>``) under the session's artifact directory. The
agents' ``--add-dir`` / ``cwd`` then point at files that match the diff exactly.

Teardown is **never** ``rm -rf``: a worktree shares the clone's object store and
(on some platforms) links large trees, so a recursive delete can corrupt or
destroy the parent clone. We always use ``git worktree remove`` (with ``--force``
for a dirtied tree) followed by ``git worktree prune`` to drop the registration.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .._gitexec import GitError, run_git

DEFAULT_CHECKOUT_TIMEOUT_SECONDS = 600.0


class WorktreeError(RuntimeError):
    """Raised when a review worktree cannot be created."""


@dataclass
class WorktreeHandle:
    """A live detached worktree plus the info needed to tear it down safely.

    ``clone_path`` is the owning clone (where ``git worktree`` commands run);
    ``path`` is the checked-out worktree the agents read. :meth:`cleanup` is
    idempotent and best-effort — a teardown failure must never fail a review.
    """

    clone_path: Path
    path: Path
    source_sha: str
    _removed: bool = False

    def cleanup(self) -> None:
        """Remove the worktree (forced) and prune its registration. Idempotent."""
        if self._removed:
            return
        self._removed = True
        try:
            run_git(
                ["worktree", "remove", "--force", str(self.path)],
                str(self.clone_path),
                timeout=60.0,
                check=False,
            )
        finally:
            run_git(["worktree", "prune"], str(self.clone_path), timeout=30.0, check=False)


def _ensure_sha_present(clone_path: Path, source_sha: str) -> None:
    """Fetch ``source_sha`` into ``clone_path`` if the object isn't already there."""
    have = run_git(["cat-file", "-e", f"{source_sha}^{{commit}}"], str(clone_path), check=False)
    if have.ok:
        return
    fetched = run_git(["fetch", "origin", source_sha], str(clone_path), timeout=300.0, check=False)
    if fetched.ok:
        return
    # Some servers reject fetch-by-SHA; fall back to a full fetch of all refs.
    run_git(["fetch", "origin"], str(clone_path), timeout=300.0, check=False)
    verify = run_git(["cat-file", "-e", f"{source_sha}^{{commit}}"], str(clone_path), check=False)
    if not verify.ok:
        raise WorktreeError(
            f"commit {source_sha} not present in {clone_path} and could not be fetched from origin"
        )


def _recover_failed_add(clone_path: Path, worktree_path: Path) -> None:
    with contextlib.suppress(Exception):
        run_git(
            ["worktree", "remove", "--force", str(worktree_path)],
            str(clone_path),
            timeout=60.0,
            check=False,
        )
    with contextlib.suppress(Exception):
        run_git(["worktree", "prune"], str(clone_path), timeout=30.0, check=False)


def add_detached_worktree(
    clone_path: Path,
    source_sha: str,
    worktree_path: Path,
    *,
    timeout_seconds: float = DEFAULT_CHECKOUT_TIMEOUT_SECONDS,
) -> WorktreeHandle:
    """Create a detached worktree of ``source_sha`` at ``worktree_path``.

    Fetches the commit first if absent. The parent directory is created; the
    worktree path itself must not already exist (git refuses a non-empty target).
    """
    clone_path = Path(clone_path)
    worktree_path = Path(worktree_path)
    _ensure_sha_present(clone_path, source_sha)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_git(
            ["worktree", "add", "--detach", str(worktree_path), source_sha],
            str(clone_path),
            timeout=timeout_seconds,
        )
    except (GitError, subprocess.TimeoutExpired) as err:
        _recover_failed_add(clone_path, worktree_path)
        detail = (
            f"timed out after {timeout_seconds:g} seconds"
            if isinstance(err, subprocess.TimeoutExpired)
            else err.stderr.strip()
        )
        raise WorktreeError(
            f"git worktree add failed for {source_sha} at {worktree_path}: {detail}"
        ) from err
    return WorktreeHandle(clone_path=clone_path, path=worktree_path, source_sha=source_sha)


#: npm/yarn/pnpm dependency lockfiles. A change to any of these means the reviewed
#: revision's dependency set may differ from what the clone has installed, so the
#: clone's ``node_modules`` must NOT be reused (it could yield a stale ``measured``).
_NPM_LOCKFILES = frozenset(
    {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"}
)


def _lockfile_proven_unchanged(clone_path: Path, base_sha: str | None, source_sha: str) -> bool:
    """True ONLY when the ``base..source`` diff is proven to touch no npm lockfile.

    Fail-safe: a missing ``base_sha`` or a failed diff returns ``False`` (not proven),
    so an undeterminable state skips the reuse rather than risking stale dependencies.
    """
    if not base_sha:
        return False
    res = run_git(
        ["diff", "--name-only", f"{base_sha}..{source_sha}"],
        str(clone_path),
        timeout=60.0,
        check=False,
    )
    if not res.ok:
        return False
    changed_names = {Path(line.strip()).name for line in res.stdout.splitlines() if line.strip()}
    return changed_names.isdisjoint(_NPM_LOCKFILES)


def _create_dir_link(target: Path, link: Path) -> bool:
    """Best-effort directory link ``link`` → ``target``. Prefers a symlink; on Windows
    (where a symlink needs privilege) falls back to a directory junction. Never raises."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                timeout=30,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def link_node_modules(
    clone_path: Path, worktree_path: Path, *, base_sha: str | None, source_sha: str
) -> bool:
    """Idempotently link ``<worktree>/node_modules`` → ``<clone>/node_modules`` so a
    freshly checked-out worktree reuses the clone's already-installed dependencies.

    CONVENIENCE ONLY — never a faithfulness mechanism, and never fails a review. Guarded
    so a reviewer never executes against stale deps: the link is created only when the
    ``base..source`` diff is proven not to touch a lockfile (:func:`_lockfile_proven_unchanged`),
    the clone actually has a ``node_modules``, and the worktree does not already have one
    (a committed ``node_modules`` or a prior link is never clobbered). Returns ``True``
    when a usable link exists after the call.
    """
    if not _lockfile_proven_unchanged(clone_path, base_sha, source_sha):
        return False
    src = Path(clone_path) / "node_modules"
    dst = Path(worktree_path) / "node_modules"
    if not src.is_dir():
        return False
    if dst.exists() or dst.is_symlink():
        return True
    return _create_dir_link(src, dst)

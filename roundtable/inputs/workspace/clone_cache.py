"""clone_cache: a bounded, GC'd store of blobless clones for review.

When the reviewed repo isn't already on disk, discovery misses and we clone it
here — into a **sibling** of the artifacts tree
(``<clone_cache_dir>/<repo-key>``), never nested under a session's artifacts, so
a session cleanup can't take the shared clone with it. ``repo-key`` is a short
hash of the normalized remote URL, so the same repo always lands in the same slot
regardless of how its URL was spelled.

Clones are **blobless** (``--filter=blob:none``): commit and tree objects are
fetched up front, file blobs on demand — cheap to create, and a detached worktree
at the review SHA hydrates exactly the blobs it needs. Review workspaces are
**never** sparse (agents read across the whole tree to understand the change).

The store is bounded (``max_gb`` / ``ttl_days``) and GC'd, but a clone that a live
review still leases — or whose ``.git`` a registered worktree still shares — is
**never** collected, so GC can't pull the object store out from under an active
worktree. All clone/fetch/GC critical sections are serialized by a per-clone
:class:`DirLock`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .._gitexec import run_git
from .locking import DirLock, Lease, acquire_lease, has_active_lease
from .remote_url import normalize_remote_url

_LOCK_SUFFIX = ".lock"

#: ``git clone`` checks out the working tree itself, so Windows long-path support
#: has to be set on the clone invocation — configuring the repo afterwards is
#: already too late for the files that clone just failed to write.
_LONGPATHS = ("-c", "core.longpaths=true")

_GIT_DIAGNOSTIC_PREFIXES = ("fatal:", "error:", "warning:", "remote: error", "hint:")


def _git_failure_detail(stderr: str, *, limit: int = 400) -> str:
    """Summarize why a git command failed, keeping diagnostics over progress noise.

    Git overwrites a single stderr progress line with ``\\r``, so the head of the
    stream is counters rather than the failure; prefer diagnostic lines and
    otherwise fall back to the tail.
    """
    lines = [line.strip() for line in stderr.replace("\r", "\n").split("\n") if line.strip()]
    if not lines:
        return ""
    diagnostics = [line for line in lines if line.lower().startswith(_GIT_DIAGNOSTIC_PREFIXES)]
    return " | ".join((diagnostics or lines)[-3:])[-limit:]


class CloneError(RuntimeError):
    """Raised when a repo cannot be cloned (including offline failure)."""


def repo_key(remote_url: str) -> str:
    """Stable short slot name for ``remote_url`` (normalized key hashed)."""
    normalized = normalize_remote_url(remote_url) or remote_url.strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    # A human-readable prefix aids debugging without affecting uniqueness.
    tail = normalized.rstrip("/").rsplit("/", 1)[-1] or "repo"
    safe_tail = "".join(c for c in tail if c.isalnum() or c in "-_.")[:32]
    return f"{safe_tail}-{digest}" if safe_tail else digest


@dataclass
class CloneCache:
    """A directory of cached blobless clones with lease-aware GC."""

    root: Path
    max_gb: float
    ttl_days: float

    def path_for(self, remote_url: str) -> Path:
        return Path(self.root) / repo_key(remote_url)

    def _lock_for(self, clone_path: Path) -> DirLock:
        return DirLock(Path(str(clone_path) + _LOCK_SUFFIX))

    def ensure(self, remote_url: str, *, source_sha: str | None = None) -> Path:
        """Return a ready durable clone for ``remote_url``, cloning if needed.

        Serialized per-clone. An existing clone is fetched to bring in
        ``source_sha`` (if given); a missing one is blobless-cloned with a
        full-clone fallback and an explicit offline failure.
        """
        clone_path = self.path_for(remote_url)
        with self._lock_for(clone_path):
            if (clone_path / ".git").exists():
                self._fetch(clone_path, source_sha)
            else:
                self._clone(remote_url, clone_path)
                self._configure(clone_path)
                self._fetch(clone_path, source_sha)
        return clone_path

    def lease(self, clone_path: Path) -> Lease:
        """Acquire an active-use lease so GC won't collect ``clone_path``."""
        return acquire_lease(clone_path)

    def _clone(self, remote_url: str, clone_path: Path) -> None:
        clone_path.parent.mkdir(parents=True, exist_ok=True)
        blobless = run_git(
            [*_LONGPATHS, "clone", "--filter=blob:none", remote_url, str(clone_path)],
            str(clone_path.parent),
            timeout=1800.0,
            check=False,
        )
        if blobless.ok:
            return
        # Older servers reject partial-clone filters — fall back to a full clone.
        _rmtree_quiet(clone_path)
        full = run_git(
            [*_LONGPATHS, "clone", remote_url, str(clone_path)],
            str(clone_path.parent),
            timeout=3600.0,
            check=False,
        )
        if not full.ok:
            _rmtree_quiet(clone_path)
            raise CloneError(
                f"could not clone {remote_url} (blobless: {_git_failure_detail(blobless.stderr)}; "
                f"full: {_git_failure_detail(full.stderr)})"
            )

    def _configure(self, clone_path: Path) -> None:
        # Persist for later worktree checkouts, which share this clone's config.
        run_git(["config", "core.longpaths", "true"], str(clone_path), check=False)

    def _fetch(self, clone_path: Path, source_sha: str | None) -> None:
        if source_sha is None:
            run_git(["fetch", "origin"], str(clone_path), timeout=600.0, check=False)
            return
        have = run_git(["cat-file", "-e", f"{source_sha}^{{commit}}"], str(clone_path), check=False)
        if have.ok:
            return
        by_sha = run_git(
            ["fetch", "origin", source_sha], str(clone_path), timeout=600.0, check=False
        )
        if by_sha.ok:
            return
        run_git(["fetch", "origin"], str(clone_path), timeout=600.0, check=False)

    def gc(self) -> list[Path]:
        """Collect clones past TTL or over the size budget; skip leased/in-use.

        Returns the paths deleted. Never touches a clone with an active lease or a
        registered (non-main) worktree.
        """
        root = Path(self.root)
        if not root.is_dir():
            return []
        clones = [p for p in root.iterdir() if p.is_dir() and not p.name.endswith(_LOCK_SUFFIX)]

        deleted: list[Path] = []
        ttl_seconds = self.ttl_days * 86400
        now = _now()

        # Age-based sweep.
        survivors: list[Path] = []
        for clone in clones:
            if self._in_use(clone):
                survivors.append(clone)
                continue
            age = now - _mtime(clone)
            if ttl_seconds > 0 and age > ttl_seconds and self._collect(clone):
                deleted.append(clone)
            else:
                survivors.append(clone)

        # Size-budget sweep (LRU): drop oldest survivors until under budget.
        budget_bytes = self.max_gb * (1024**3)
        if budget_bytes > 0:
            sized = sorted(((c, _dir_size(c)) for c in survivors), key=lambda cs: _mtime(cs[0]))
            total = sum(size for _c, size in sized)
            for clone, size in sized:
                if total <= budget_bytes:
                    break
                if self._in_use(clone):
                    continue
                if self._collect(clone):
                    deleted.append(clone)
                    total -= size
        return deleted

    def _in_use(self, clone_path: Path) -> bool:
        if has_active_lease(clone_path):
            return True
        return _has_live_worktree(clone_path)

    def _collect(self, clone_path: Path) -> bool:
        """Remove a clone under its lock, re-checking in-use to avoid a race."""
        lock = self._lock_for(clone_path)
        try:
            lock.acquire()
        except Exception:
            return False
        try:
            if self._in_use(clone_path):
                return False
            _rmtree_quiet(clone_path)
            return not clone_path.exists()
        finally:
            lock.release()


def _has_live_worktree(clone_path: Path) -> bool:
    """True if the clone has any linked worktree besides its own main tree."""
    run_git(["worktree", "prune"], str(clone_path), check=False)
    listed = run_git(["worktree", "list", "--porcelain"], str(clone_path), check=False)
    if not listed.ok:
        return False
    # Each worktree is a block starting with "worktree <path>"; >1 block => linked.
    return listed.stdout.count("worktree ") > 1


def _rmtree_quiet(path: Path) -> None:
    """Delete a clone tree, clearing read-only bits git sets on pack objects.

    On Windows ``shutil.rmtree`` fails on read-only files (git marks objects
    read-only), so ``ignore_errors`` would silently leave the clone behind and GC
    would never reclaim space. The handler chmods the offending path writable and
    retries via the ``onexc`` hook (Python 3.12+).
    """
    if not Path(path).exists():
        return
    shutil.rmtree(path, onexc=_on_rm_error)


def _on_rm_error(func, path, _exc) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _dir_size(path: Path) -> int:
    total = 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(current, name))
    return total


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _now() -> float:
    import time

    return time.time()

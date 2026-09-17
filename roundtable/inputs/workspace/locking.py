"""locking: a cross-platform, dependency-free advisory lock and lease.

The clone cache is shared mutable state — several review sessions (possibly
different processes on the same machine) may clone, fetch, or GC the same repo
concurrently. Two primitives guard it:

* :class:`DirLock` — a mutual-exclusion lock built on the one filesystem
  operation that is atomic on every OS (``os.mkdir``). Held only briefly around a
  clone/fetch/GC critical section. A lock older than ``stale_after`` is assumed
  abandoned by a crashed process and stolen, so a crash can't wedge the cache
  forever.
* :class:`Lease` — a long-lived "this clone is in active use" marker (a file with
  an expiry) that outlives the lock and spans an entire review, so GC never
  deletes a clone whose ``.git`` a live worktree still shares.
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from roundtable.runtime import APP_DOT_SLUG

_LEASE_DIRNAME = f".{APP_DOT_SLUG}-leases"


class LockTimeout(RuntimeError):
    """Raised when a :class:`DirLock` cannot be acquired within its timeout."""


@dataclass
class DirLock:
    """An atomic ``mkdir``-based advisory lock with stale-lock recovery."""

    lock_dir: Path
    timeout: float = 120.0
    poll: float = 0.1
    # Must exceed the longest critical section a holder can legitimately run under
    # this lock — the full clone (``clone_cache._clone`` ``timeout=3600``) — so a
    # slow-but-live clone is never mistaken for an abandoned one and stolen
    # mid-flight. Sized to the lease TTL for headroom; only a genuinely crashed
    # holder is reclaimed, after this window.
    stale_after: float = 6 * 3600.0
    _held: bool = False

    def acquire(self) -> DirLock:
        deadline = time.monotonic() + self.timeout
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                os.mkdir(self.lock_dir)
                self._held = True
                with contextlib.suppress(OSError):
                    (self.lock_dir / "owner").write_text(
                        f"{os.getpid()}\n{time.time()}\n", encoding="utf-8"
                    )
                return self
            except FileExistsError:
                if self._steal_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"could not acquire lock {self.lock_dir} within {self.timeout}s"
                    ) from None
                time.sleep(self.poll)

    def _steal_if_stale(self) -> bool:
        try:
            age = time.time() - self.lock_dir.stat().st_mtime
        except OSError:
            return False
        if age <= self.stale_after:
            return False
        try:
            _rmdir_lock(self.lock_dir)
            return True
        except OSError:
            return False

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        with contextlib.suppress(OSError):
            _rmdir_lock(self.lock_dir)

    def __enter__(self) -> DirLock:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def _rmdir_lock(lock_dir: Path) -> None:
    owner = lock_dir / "owner"
    with contextlib.suppress(OSError):
        owner.unlink()
    os.rmdir(lock_dir)


@dataclass
class Lease:
    """An active-use marker on a clone; released at the end of a review."""

    path: Path
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        with contextlib.suppress(OSError):
            self.path.unlink()

    def __enter__(self) -> Lease:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire_lease(clone_path: Path, *, ttl_seconds: float = 6 * 3600) -> Lease:
    """Register an active-use lease on ``clone_path`` valid for ``ttl_seconds``.

    The expiry is a crash backstop: a review that dies without releasing leaves a
    file that GC ignores only *after* it expires, so a crashed lease can't pin a
    clone forever.
    """
    lease_dir = Path(clone_path) / _LEASE_DIRNAME
    lease_dir.mkdir(parents=True, exist_ok=True)
    lease_path = lease_dir / f"{uuid.uuid4().hex}.lease"
    expiry = time.time() + ttl_seconds
    lease_path.write_text(f"{os.getpid()}\n{expiry}\n", encoding="utf-8")
    return Lease(path=lease_path)


def has_active_lease(clone_path: Path) -> bool:
    """True if ``clone_path`` has any unexpired lease; reaps expired ones."""
    lease_dir = Path(clone_path) / _LEASE_DIRNAME
    if not lease_dir.is_dir():
        return False
    now = time.time()
    active = False
    for lease in lease_dir.glob("*.lease"):
        try:
            expiry = float(lease.read_text(encoding="utf-8").splitlines()[1])
        except (OSError, IndexError, ValueError):
            # Unreadable lease — treat as active (fail safe: don't GC).
            active = True
            continue
        if expiry > now:
            active = True
        else:
            try:
                lease.unlink()
            except OSError:
                active = True
    return active

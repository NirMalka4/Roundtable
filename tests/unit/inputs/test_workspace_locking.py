"""Unit tests for the cross-platform dir-lock and lease primitives."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from roundtable.inputs.workspace.locking import (
    DirLock,
    LockTimeout,
    acquire_lease,
    has_active_lease,
)


def test_dirlock_is_mutually_exclusive(tmp_path: Path) -> None:
    lock_dir = tmp_path / "x.lock"
    held = DirLock(lock_dir).acquire()
    try:
        with pytest.raises(LockTimeout):
            DirLock(lock_dir, timeout=0.3, poll=0.05).acquire()
    finally:
        held.release()
    # Once released, it can be re-acquired.
    DirLock(lock_dir, timeout=1.0).acquire().release()


def test_dirlock_steals_stale_lock(tmp_path: Path) -> None:
    lock_dir = tmp_path / "x.lock"
    DirLock(lock_dir).acquire()  # leak it (simulate a crashed holder)
    os.utime(lock_dir, (0, 0))
    stealer = DirLock(lock_dir, timeout=1.0, stale_after=1.0).acquire()
    stealer.release()
    assert not lock_dir.exists()


def test_default_stale_window_exceeds_longest_clone(tmp_path: Path) -> None:
    # Regression: a slow-but-live clone/fetch (up to the 3600s full-clone timeout in
    # clone_cache) must never be mistaken for abandoned and stolen mid-flight. The
    # default stale window has to exceed that longest legitimate hold.
    assert DirLock(tmp_path / "x.lock").stale_after >= 3600.0
    lock_dir = tmp_path / "y.lock"
    held = DirLock(lock_dir).acquire()
    try:
        # A second acquirer using the default stale window times out (does not steal).
        with pytest.raises(LockTimeout):
            DirLock(lock_dir, timeout=0.3, poll=0.05).acquire()
    finally:
        held.release()


def test_lease_lifecycle(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    assert has_active_lease(clone) is False
    lease = acquire_lease(clone)
    assert has_active_lease(clone) is True
    lease.release()
    assert has_active_lease(clone) is False


def test_expired_lease_is_reaped(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    acquire_lease(clone, ttl_seconds=-1)  # already expired
    assert has_active_lease(clone) is False

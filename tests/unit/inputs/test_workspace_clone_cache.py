"""Unit tests for the blobless clone cache with lease-aware GC."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from roundtable.inputs.workspace import clone_cache as clone_cache_module
from roundtable.inputs.workspace.clone_cache import (
    CloneCache,
    CloneError,
    _git_failure_detail,
    repo_key,
)
from roundtable.inputs.workspace.worktree import add_detached_worktree


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")


def _source_repo(path: Path) -> str:
    """A normal repo usable as a clone source; returns its HEAD sha."""
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@e.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "c1")
    return _git(path, "rev-parse", "HEAD").strip()


def _cache(tmp_path: Path, **kw) -> CloneCache:
    return CloneCache(
        root=tmp_path / "cache",
        max_gb=kw.get("max_gb", 100.0),
        ttl_days=kw.get("ttl_days", 30.0),
    )


def test_repo_key_stable_across_url_spellings() -> None:
    a = repo_key("https://dev.azure.com/org/proj/_git/Repo.git")
    b = repo_key("git@ssh.dev.azure.com:v3/org/proj/Repo")
    assert a == b


def test_repo_key_shared_by_both_ado_hosts() -> None:
    """One repo must occupy one cache slot however its PR URL spelled the host."""
    a = repo_key("https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo")
    b = repo_key("https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo")
    assert a == b


def test_ensure_clones_then_reuses(tmp_path: Path) -> None:
    src = tmp_path / "src"
    head = _source_repo(src)
    cache = _cache(tmp_path)

    clone = cache.ensure(str(src), source_sha=head)
    assert (clone / ".git").exists()
    assert clone == cache.path_for(str(src))

    # Second call reuses the same directory (no re-clone).
    clone2 = cache.ensure(str(src), source_sha=head)
    assert clone2 == clone


def test_ensure_offline_failure_raises(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(CloneError):
        cache.ensure(str(tmp_path / "does-not-exist"))


def test_clone_enables_longpaths_on_the_clone_itself(tmp_path: Path, monkeypatch) -> None:
    """``git clone`` performs its own checkout, so configuring the repo after is too late."""
    src = tmp_path / "src"
    head = _source_repo(src)
    cache = _cache(tmp_path)
    invocations: list[list[str]] = []
    real_run_git = clone_cache_module.run_git

    def _spy(args, cwd, **kwargs):
        invocations.append(list(args))
        return real_run_git(args, cwd, **kwargs)

    monkeypatch.setattr(clone_cache_module, "run_git", _spy)
    cache.ensure(str(src), source_sha=head)

    clones = [args for args in invocations if "clone" in args]
    assert clones
    assert all(args[:2] == ["-c", "core.longpaths=true"] for args in clones)


def test_clone_failure_reports_the_git_diagnostic(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(CloneError, match="fatal:"):
        cache.ensure(str(tmp_path / "does-not-exist"))


def test_failure_detail_prefers_diagnostics_over_progress_noise() -> None:
    progress = "".join(f"Updating files:  {pct}% ({pct}/47435)\r" for pct in range(1, 97))
    stderr = (
        "Cloning into 'C:/clones/repo'...\r"
        + progress
        + "error: unable to create file deep/nested/name.tsx: Filename too long\n"
        + "fatal: unable to checkout working tree\n"
    )
    detail = _git_failure_detail(stderr)
    assert "Filename too long" in detail
    assert "fatal: unable to checkout working tree" in detail
    assert "Updating files" not in detail


def test_failure_detail_falls_back_to_the_tail() -> None:
    detail = _git_failure_detail("one\ntwo\nthree\nfour\nfive\n")
    assert "five" in detail
    assert "one" not in detail


def test_gc_collects_old_unleased_clone(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _source_repo(src)
    cache = _cache(tmp_path, ttl_days=1.0)
    clone = cache.ensure(str(src))

    # Age the clone past its TTL.
    old = time.time() - 3 * 86400
    os.utime(clone, (old, old))
    deleted = cache.gc()
    assert clone in deleted
    assert not clone.exists()


def test_gc_skips_leased_clone(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _source_repo(src)
    cache = _cache(tmp_path, ttl_days=1.0)
    clone = cache.ensure(str(src))
    lease = cache.lease(clone)
    try:
        old = time.time() - 3 * 86400
        os.utime(clone, (old, old))
        assert cache.gc() == []
        assert clone.exists()
    finally:
        lease.release()


def test_gc_skips_clone_with_live_worktree(tmp_path: Path) -> None:
    src = tmp_path / "src"
    head = _source_repo(src)
    cache = _cache(tmp_path, ttl_days=1.0)
    clone = cache.ensure(str(src), source_sha=head)
    handle = add_detached_worktree(clone, head, tmp_path / "session" / "wt")
    try:
        old = time.time() - 3 * 86400
        os.utime(clone, (old, old))
        assert cache.gc() == []
        assert clone.exists()
    finally:
        handle.cleanup()

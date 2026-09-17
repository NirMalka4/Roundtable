"""Unit tests for the workspace factory — settings→cache bridge and reconcile."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from roundtable.inputs.workspace import factory
from roundtable.settings.workspace import WorkspaceSettings


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")


def _source_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@e.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "c1")
    return _git(path, "rev-parse", "HEAD").strip()


def _settings(cache_dir: Path, **kw) -> WorkspaceSettings:
    return WorkspaceSettings(
        clone_cache_dir=str(cache_dir),
        clone_cache_max_gb=kw.get("max_gb", 100),
        clone_cache_ttl_days=kw.get("ttl_days", 30),
    )


def test_reconcile_orphans_collects_expired_unleased_clone(tmp_path: Path) -> None:
    src = tmp_path / "src"
    head = _source_repo(src)
    settings = _settings(tmp_path / "cache", ttl_days=1)

    cache = factory.build_clone_cache(settings)
    clone = cache.ensure(str(src), source_sha=head)
    assert clone.exists()

    # Age the clone two days so the 1-day TTL sweep is eligible to collect it.
    old = time.time() - 2 * 86400
    os.utime(clone, (old, old))

    deleted = factory.reconcile_orphans(settings)
    assert clone in deleted
    assert not clone.exists()


def test_reconcile_orphans_never_raises_on_missing_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "does-not-exist")
    # No cache directory yet — reconcile must be a silent no-op, never raise.
    assert factory.reconcile_orphans(settings) == []


def test_build_review_workspace_propagates_checkout_timeout(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    settings = _settings(tmp_path / "cache")
    settings = type(settings)(
        clone_cache_dir=settings.clone_cache_dir,
        clone_cache_max_gb=settings.clone_cache_max_gb,
        clone_cache_ttl_days=settings.clone_cache_ttl_days,
        checkout_timeout_seconds=900,
    )
    monkeypatch.setattr(
        factory,
        "resolve_workspace",
        lambda request, **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )

    factory.build_review_workspace(
        SimpleNamespace(),
        settings=settings,
        worktree_dir=tmp_path / "worktree",
    )

    assert captured["checkout_timeout_seconds"] == 900

"""Unit tests for the ReviewWorkspace resolver façade."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roundtable.inputs.workspace import (
    MODE_CLONE_WORKTREE,
    MODE_LIVE_CHECKOUT,
    MODE_LOCAL_WORKTREE,
    CloneCache,
    RepoRegistry,
    WorkspaceError,
    WorkspaceRequest,
    resolve_workspace,
)

REMOTE = "https://dev.azure.com/org/proj/_git/Repo"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")


def _repo_with_two_commits(path: Path, remote: str | None = None) -> tuple[str, str]:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@e.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "c1")
    base = _git(path, "rev-parse", "HEAD").strip()
    (path / "f.txt").write_text("v2\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "c2")
    head = _git(path, "rev-parse", "HEAD").strip()
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return base, head


def _cache(tmp_path: Path) -> CloneCache:
    return CloneCache(root=tmp_path / "cache", max_gb=100.0, ttl_days=30.0)


def test_local_mode_reviews_in_place(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _base, head = _repo_with_two_commits(repo)
    ws = resolve_workspace(
        WorkspaceRequest(mode="local", repo_path=str(repo), source_sha=head),
        cache=_cache(tmp_path),
        worktree_dir=tmp_path / "unused",
    )
    assert ws.mode == MODE_LIVE_CHECKOUT
    assert ws.path == repo
    ws.cleanup()
    assert repo.exists()  # in-place review never deletes the repo


def test_pr_mode_uses_discovered_local_clone(tmp_path: Path) -> None:
    # A clone found on disk (by remote identity) => local-worktree, no cache clone.
    on_disk = tmp_path / "repos" / "the-clone"
    base, _head = _repo_with_two_commits(on_disk, remote=REMOTE)

    ws = resolve_workspace(
        WorkspaceRequest(mode="pr", remote_url=REMOTE, source_sha=base),
        cache=_cache(tmp_path),
        worktree_dir=tmp_path / "session" / "wt",
        search_paths=[str(tmp_path / "repos")],
    )
    try:
        assert ws.mode == MODE_LOCAL_WORKTREE
        assert ws.clone_path == on_disk
        assert (ws.path / "f.txt").read_text(encoding="utf-8") == "v1\n"  # at source SHA
    finally:
        ws.cleanup()
    assert not ws.path.exists()  # worktree torn down
    assert on_disk.exists()  # user's clone untouched


def test_pr_mode_clones_on_discovery_miss(tmp_path: Path) -> None:
    # No local clone under search paths => clone into cache => clone-worktree.
    source = tmp_path / "source"
    _base, head = _repo_with_two_commits(source)  # acts as the remote to clone
    registry = RepoRegistry.load(tmp_path / "cache")

    ws = resolve_workspace(
        WorkspaceRequest(mode="pr", remote_url=str(source), source_sha=head),
        cache=_cache(tmp_path),
        worktree_dir=tmp_path / "session" / "wt",
        search_paths=[str(tmp_path / "empty")],
        registry=registry,
    )
    try:
        assert ws.mode == MODE_CLONE_WORKTREE
        assert ws.clone_path is not None and ws.clone_path.exists()
        assert (ws.path / "f.txt").read_text(encoding="utf-8") == "v2\n"
    finally:
        ws.cleanup()
    assert not ws.path.exists()


def test_missing_required_fields_raise(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(WorkspaceError):
        resolve_workspace(WorkspaceRequest(mode="local"), cache=cache, worktree_dir=tmp_path)
    with pytest.raises(WorkspaceError):
        resolve_workspace(
            WorkspaceRequest(mode="pr", remote_url=REMOTE), cache=cache, worktree_dir=tmp_path
        )

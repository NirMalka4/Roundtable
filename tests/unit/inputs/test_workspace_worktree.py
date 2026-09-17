"""Unit tests for detached review-worktree lifecycle."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roundtable.inputs._gitexec import GitResult
from roundtable.inputs.workspace import worktree as worktree_module
from roundtable.inputs.workspace.worktree import (
    WorktreeError,
    add_detached_worktree,
    link_node_modules,
)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")


def _make_clone_with_two_commits(path: Path) -> tuple[str, str]:
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
    return base, head


def test_worktree_checks_out_requested_sha(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    base, _head = _make_clone_with_two_commits(clone)
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, base, wt)
    try:
        assert (wt / "f.txt").read_text(encoding="utf-8") == "v1\n"
        assert handle.source_sha == base
    finally:
        handle.cleanup()


def test_cleanup_removes_worktree_and_is_idempotent(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _base, head = _make_clone_with_two_commits(clone)
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, head, wt)
    assert wt.exists()
    handle.cleanup()
    assert not wt.exists()
    handle.cleanup()  # second call is a no-op, must not raise
    # The clone itself is untouched.
    assert (clone / ".git").exists()


def test_cleanup_forces_removal_of_dirty_tree(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _base, head = _make_clone_with_two_commits(clone)
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, head, wt)
    (wt / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")  # dirty the tree
    handle.cleanup()
    assert not wt.exists()


def test_missing_sha_raises(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _make_clone_with_two_commits(clone)
    wt = tmp_path / "session" / "worktree"
    with pytest.raises(WorktreeError):
        add_detached_worktree(clone, "0" * 40, wt)


def test_checkout_timeout_is_propagated_and_recovers_registration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    worktree = tmp_path / "session" / "worktree"
    calls: list[tuple[list[str], float, bool]] = []

    def run_git(args, cwd, *, timeout=30.0, check=True):
        calls.append((list(args), timeout, check))
        if args[:2] == ["cat-file", "-e"]:
            return GitResult("", "", 0)
        if args[:2] == ["worktree", "add"]:
            raise subprocess.TimeoutExpired(["git", *args], timeout)
        return GitResult("", "", 0)

    monkeypatch.setattr(worktree_module, "run_git", run_git)

    with pytest.raises(WorktreeError, match="timed out after 900 seconds"):
        add_detached_worktree(clone, "a" * 40, worktree, timeout_seconds=900)

    assert calls[1][1] == 900
    assert calls[2][0][:3] == ["worktree", "remove", "--force"]
    assert calls[3][0] == ["worktree", "prune"]


def _add_commit(path: Path, filename: str, body: str) -> str:
    (path / filename).write_text(body, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", f"add {filename}")
    return _git(path, "rev-parse", "HEAD").strip()


def _seed_node_modules(clone: Path) -> Path:
    nm = clone / "node_modules"
    nm.mkdir()
    (nm / "marker.txt").write_text("installed\n", encoding="utf-8")
    return nm


def test_link_node_modules_links_when_lockfile_unchanged(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    base, head = _make_clone_with_two_commits(clone)  # f.txt v1 -> v2, no lockfile
    _seed_node_modules(clone)
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, head, wt)
    try:
        linked = link_node_modules(clone, wt, base_sha=base, source_sha=head)
        assert linked is True
        # The worktree now reads the clone's installed dependency through the link.
        assert (wt / "node_modules" / "marker.txt").read_text(encoding="utf-8") == "installed\n"
    finally:
        handle.cleanup()


def test_link_node_modules_skipped_when_lockfile_changed(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    base, _head = _make_clone_with_two_commits(clone)
    head = _add_commit(clone, "package-lock.json", '{"lockfileVersion": 3}\n')
    _seed_node_modules(clone)
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, head, wt)
    try:
        # base..head touches package-lock.json -> deps may differ -> never reuse.
        assert link_node_modules(clone, wt, base_sha=base, source_sha=head) is False
        assert not (wt / "node_modules").exists()
    finally:
        handle.cleanup()


def test_link_node_modules_skipped_without_base_sha(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    _base, head = _make_clone_with_two_commits(clone)
    _seed_node_modules(clone)
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, head, wt)
    try:
        # Undeterminable diff (no base) is not "proven unchanged" -> skip.
        assert link_node_modules(clone, wt, base_sha=None, source_sha=head) is False
        assert not (wt / "node_modules").exists()
    finally:
        handle.cleanup()


def test_link_node_modules_noop_when_clone_has_none(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    base, head = _make_clone_with_two_commits(clone)  # no node_modules seeded
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, head, wt)
    try:
        assert link_node_modules(clone, wt, base_sha=base, source_sha=head) is False
        assert not (wt / "node_modules").exists()
    finally:
        handle.cleanup()


def test_link_node_modules_never_clobbers_existing(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    base, head = _make_clone_with_two_commits(clone)
    _seed_node_modules(clone)
    wt = tmp_path / "session" / "worktree"

    handle = add_detached_worktree(clone, head, wt)
    try:
        # A pre-existing (e.g. committed) node_modules must be preserved verbatim.
        existing = wt / "node_modules"
        existing.mkdir()
        (existing / "own.txt").write_text("committed\n", encoding="utf-8")
        assert link_node_modules(clone, wt, base_sha=base, source_sha=head) is True
        assert (existing / "own.txt").read_text(encoding="utf-8") == "committed\n"
        assert not (existing / "marker.txt").exists()  # clone's was NOT linked over it
    finally:
        handle.cleanup()

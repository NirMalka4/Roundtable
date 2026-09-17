"""Unit tests for local-clone discovery by remote identity."""

from __future__ import annotations

import subprocess
from pathlib import Path

from roundtable.inputs.workspace import RepoRegistry, discover_clone

REMOTE = "https://dev.azure.com/org/proj/_git/Repo"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")


def _make_clone(path: Path, remote: str | None) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@e.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("x\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "c")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return path


def test_discovers_by_remote_not_folder_name(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    # Folder name deliberately unrelated to the repo name in the remote.
    _make_clone(root / "totally-different-name", REMOTE)
    _make_clone(root / "decoy", "https://dev.azure.com/org/proj/_git/Other")

    result = discover_clone(REMOTE, search_paths=[str(root)])
    assert result.path is not None
    assert result.path.name == "totally-different-name"
    assert result.source == "search_path"
    assert len(result.candidates) == 1


def test_miss_when_no_clone_matches(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    _make_clone(root / "decoy", "https://dev.azure.com/org/proj/_git/Other")
    result = discover_clone(REMOTE, search_paths=[str(root)])
    assert result.path is None
    assert result.source == "miss"


def test_registry_hit_skips_scan(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    clone = _make_clone(root / "the-clone", REMOTE)
    reg = RepoRegistry.load(tmp_path / "cache")
    reg.record("dev.azure.com/org/proj/repo", clone)

    # Empty search paths: only a registry hit can succeed here.
    result = discover_clone(REMOTE, search_paths=[str(tmp_path / "nonexistent")], registry=reg)
    assert result.source == "registry"
    assert result.path == clone


def test_scan_records_into_registry(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    clone = _make_clone(root / "the-clone", REMOTE)
    reg = RepoRegistry.load(tmp_path / "cache")

    discover_clone(REMOTE, search_paths=[str(root)], registry=reg)
    assert reg.lookup("dev.azure.com/org/proj/repo") == clone


def test_worktrees_of_same_clone_count_once(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    clone = _make_clone(root / "the-clone", REMOTE)
    wt = tmp_path / "repos" / "wt"
    _git(clone, "worktree", "add", "--detach", str(wt))

    result = discover_clone(REMOTE, search_paths=[str(root)])
    # A repo and its linked worktree share a git-common-dir -> one distinct clone.
    assert len(result.candidates) == 1
    assert result.path is not None

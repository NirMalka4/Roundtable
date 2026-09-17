"""Tests for git_context: the unified diff gatherer (real temp git repos)."""

from __future__ import annotations

from pathlib import Path

import pytest
from _gitrepo import GitRepo, _git, _init_repo, _write

from roundtable.inputs.git_context import (
    ReviewContextOptions,
    determine_base_branch,
    extract_changed_files,
    gather_review_context,
    resolve_default_base_branch,
    resolve_target_ref,
)


# ── gather_review_context (happy path) ──────────────────────────────────────
def test_gather_header_and_sections(feature_repo: GitRepo):
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(feature_repo.path),
            repo_name="TestRepo",
            target_branch="main",
            source_branch="feature",
        )
    )
    assert res.git_context.startswith("=== Repository: TestRepo | Base: main | HEAD: feature ===")
    assert "-- Changed Files --" in res.git_context
    assert "-- Diff --" in res.git_context
    assert "diff --git" in res.git_context


def test_gather_changed_files_rename_aware(feature_repo: GitRepo):
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(feature_repo.path),
            repo_name="TestRepo",
            target_branch="main",
            source_branch="feature",
        )
    )
    assert "calc.py" in res.changed_files
    assert "util/new_helper.py" in res.changed_files
    assert "README.md" not in res.changed_files


def test_gather_metadata_sha_pinning(feature_repo: GitRepo):
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(feature_repo.path),
            repo_name="TestRepo",
            target_branch="main",
            source_branch="feature",
        )
    )
    md = res.metadata
    assert md.snapshot_sha == feature_repo.head_sha
    # merge-base(main, HEAD) == the base commit on main.
    assert md.base_sha == feature_repo.base_sha
    assert md.diff_command == "git diff -M main...HEAD"
    assert md.changed_file_count == 2
    assert md.diff_line_count > 0
    assert md.mode == "live-checkout"


def test_gather_history_present(feature_repo: GitRepo):
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(feature_repo.path),
            repo_name="TestRepo",
            target_branch="main",
            source_branch="feature",
        )
    )
    assert "=== Repository: TestRepo ===" in res.git_history
    assert "calc.py" in res.git_history


def test_gather_empty_diff(tmp_path: Path):
    repo = tmp_path / "Empty"
    _init_repo(repo)
    _write(repo, "a.txt", "hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")  # no changes vs main

    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(repo),
            repo_name="Empty",
            target_branch="main",
            source_branch="feature",
        )
    )
    assert res.git_context == "No changes detected."
    assert res.changed_files == []
    assert res.metadata.changed_file_count == 0


def test_rename_detection_uses_new_path(tmp_path: Path):
    repo = tmp_path / "Rename"
    _init_repo(repo)
    _write(repo, "old_name.py", "x = 1\n" * 20)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    _git(repo, "mv", "old_name.py", "new_name.py")
    _git(repo, "commit", "-m", "rename")

    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(repo),
            repo_name="Rename",
            target_branch="main",
            source_branch="feature",
        )
    )
    # -M detects the rename; changed_files uses the b/ (new) side.
    assert "new_name.py" in res.changed_files


def test_gather_invalid_worktree_degrades_gracefully(tmp_path: Path):
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(tmp_path / "does-not-exist"),
            repo_name="Nope",
            target_branch="main",
            source_branch="feature",
        )
    )
    assert res.git_context.startswith("=== Repository: Nope | Base: main | HEAD: feature")
    assert "Failed to capture git context" in res.git_context
    assert res.metadata.base_sha is None


# ── resolve_target_ref ──────────────────────────────────────────────────────
def test_resolve_target_ref_local_branch(feature_repo: GitRepo):
    # No origin remote -> resolves to the bare local branch name.
    assert resolve_target_ref(str(feature_repo.path), "main") == "main"


def test_resolve_target_ref_strips_prefixes(feature_repo: GitRepo):
    assert resolve_target_ref(str(feature_repo.path), "refs/heads/main") == "main"
    assert resolve_target_ref(str(feature_repo.path), "origin/main") == "main"


def test_resolve_target_ref_unknown_raises(feature_repo: GitRepo):
    with pytest.raises(ValueError, match="not found"):
        resolve_target_ref(str(feature_repo.path), "no-such-branch")


# ── determine_base_branch ───────────────────────────────────────────────────
def test_determine_base_branch_default(feature_repo: GitRepo):
    # No remote configured -> falls through to the origin/main default.
    assert determine_base_branch(str(feature_repo.path)) == "origin/main"


# ── resolve_default_base_branch (the detection SoT) ─────────────────────────
def test_resolve_default_base_local_main(feature_repo: GitRepo):
    # Local `main` (no origin) is probed as a bare ref and returned bare.
    assert resolve_default_base_branch(str(feature_repo.path)) == "main"


def test_resolve_default_base_local_master(tmp_path: Path):
    repo = tmp_path / "master-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _write(repo, "README.md", "hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    # No origin/HEAD, no `main` -> falls to the `master` candidate (bare local).
    assert resolve_default_base_branch(str(repo)) == "master"


def test_resolve_default_base_prefers_origin_head(tmp_path: Path):
    origin = tmp_path / "origin-repo"
    origin.mkdir()
    _git(origin, "init", "-b", "develop")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    _write(origin, "README.md", "hello\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", "initial")
    clone = tmp_path / "clone-repo"
    _git(tmp_path, "clone", str(origin), str(clone))
    # A fresh clone sets refs/remotes/origin/HEAD -> origin/develop.
    assert resolve_default_base_branch(str(clone)) == "develop"


def test_resolve_default_base_none_when_no_candidate(feature_repo: GitRepo):
    # Restrict candidates to names that do not exist -> undetectable.
    assert (
        resolve_default_base_branch(str(feature_repo.path), candidate_names=("nonexistent",))
        is None
    )


def test_resolve_default_base_honours_custom_candidates(feature_repo: GitRepo):
    # `feature` is the HEAD branch; it must be probeable via a custom list.
    assert (
        resolve_default_base_branch(str(feature_repo.path), candidate_names=("feature",))
        == "feature"
    )


# ── extract_changed_files (pure, b/ side) ───────────────────────────────────
def test_extract_changed_files_b_side_and_dedup():
    diff = (
        "diff --git a/old.py b/renamed.py\n"
        "similarity index 100%\n"
        "diff --git a/x.py b/x.py\n"
        "index 1..2 100644\n"
        "diff --git a/x.py b/x.py\n"  # duplicate
    )
    assert extract_changed_files(diff) == ["renamed.py", "x.py"]


# ── source_sha / mode overrides (the --pr / URL workspace path) ─────────────
def _two_commit_feature(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo whose ``feature`` HEAD has two commits over ``main``.

    Returns (repo, first_sha, head_sha). ``first_sha`` touches only ``a.py``;
    the head commit additionally touches ``b.py``.
    """
    repo = tmp_path / "TwoCommit"
    _init_repo(repo)
    _write(repo, "a.py", "x = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    _write(repo, "a.py", "x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "first")
    first_sha = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "b.py", "y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "second")
    head_sha = _git(repo, "rev-parse", "HEAD").strip()
    return repo, first_sha, head_sha


def test_source_sha_anchors_diff_not_head(tmp_path: Path):
    repo, first_sha, _head_sha = _two_commit_feature(tmp_path)
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(repo),
            repo_name="TwoCommit",
            target_branch="main",
            source_branch="feature",
            source_sha=first_sha,
        )
    )
    # Diff is pinned to first_sha: only a.py changed, b.py (head-only) is absent.
    assert res.changed_files == ["a.py"]
    assert "b.py" not in res.changed_files
    assert res.metadata.snapshot_sha == first_sha
    assert res.metadata.diff_command == f"git diff -M main...{first_sha}"


def test_absent_source_sha_defaults_to_head(tmp_path: Path):
    repo, _first_sha, head_sha = _two_commit_feature(tmp_path)
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(repo),
            repo_name="TwoCommit",
            target_branch="main",
            source_branch="feature",
        )
    )
    assert set(res.changed_files) == {"a.py", "b.py"}
    assert res.metadata.snapshot_sha == head_sha
    assert res.metadata.diff_command == "git diff -M main...HEAD"


def test_explicit_mode_is_honored(tmp_path: Path):
    repo, first_sha, _head = _two_commit_feature(tmp_path)
    res = gather_review_context(
        ReviewContextOptions(
            worktree_path=str(repo),
            repo_name="TwoCommit",
            target_branch="main",
            source_branch="feature",
            source_sha=first_sha,
            mode="clone-worktree",
        )
    )
    # The workspace resolver's authoritative mode is threaded straight through.
    assert res.metadata.mode == "clone-worktree"

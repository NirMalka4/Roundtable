"""Tests for repo_detector.detect_repos (real temp git repos)."""

from __future__ import annotations

from pathlib import Path

from _gitrepo import GitRepo

from roundtable.inputs.repo_detector import detect_repos


def test_detect_repo_name_branch(feature_repo: GitRepo):
    repos = detect_repos(str(feature_repo.path))
    assert len(repos) == 1
    info = repos[0]
    assert info.name == "TestRepo"
    assert info.branch == "feature"
    assert Path(info.root_path).resolve() == feature_repo.path.resolve()
    assert info.remote_url is None  # no origin configured


def test_detect_repo_from_subdir(feature_repo: GitRepo):
    subdir = feature_repo.path / "util"
    repos = detect_repos(str(subdir))
    assert len(repos) == 1
    assert repos[0].name == "TestRepo"


def test_detect_non_git_dir(tmp_path: Path):
    assert detect_repos(str(tmp_path)) == []

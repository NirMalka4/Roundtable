"""Unit tests for cli._resolve_review_target — URL bootstrap + local mode."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from roundtable import cli

_UNSET = object()


def _args(tmp_path: Path, *, pr: str | None, repo: object = _UNSET) -> argparse.Namespace:
    repo_val = str(tmp_path) if repo is _UNSET else repo
    return argparse.Namespace(repo=repo_val, pr=pr, artifacts_dir=None)


def _stub_common(monkeypatch) -> list:
    """Stub the shared collaborators; return the workspaces build was asked for."""
    monkeypatch.setattr(cli, "reconcile_orphans", lambda _s: [])
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(workspace=object()))
    built: list = []

    def _build(request, **kw):
        built.append(request)
        return SimpleNamespace(
            path=Path(kw["worktree_dir"]).parent,
            mode="clone-worktree" if request.mode == "pr" else "local-checkout",
            source_sha="s" * 40,
            base_sha="b" * 40,
            cleanup=lambda: None,
        )

    monkeypatch.setattr(cli, "build_review_workspace", _build)
    return built


def test_resolve_pr_url_bootstraps_without_local_repo(monkeypatch, tmp_path: Path) -> None:
    built = _stub_common(monkeypatch)
    # No local clone at --repo, and the materialized workspace also fails detection
    # → the PR fallback repo descriptor must be used.
    monkeypatch.setattr(cli, "detect_repos", lambda _p: [])
    pr = SimpleNamespace(pr_id=42, repo_name="Repo")
    monkeypatch.setattr(cli, "parse_pr_reference", lambda ref, hint: pr)
    monkeypatch.setattr(
        cli,
        "fetch_pr_metadata",
        lambda _pr: SimpleNamespace(
            source_branch="feature",
            target_branch="main",
            source_commit_sha="s" * 40,
            target_commit_sha="t" * 40,
        ),
    )
    monkeypatch.setattr(cli, "ado_clone_url", lambda _pr: "https://ado/Repo")

    target = cli._resolve_review_target(
        _args(tmp_path, pr="https://ado/_git/Repo/pullrequest/42"), tmp_path / "arts"
    )

    assert target.pr is pr
    assert target.metadata.source_branch == "feature"
    assert target.repo.name == "Repo"  # fallback descriptor
    assert built[0].mode == "pr"
    assert built[0].source_sha == "s" * 40
    assert built[0].base_sha == "t" * 40


def test_resolve_local_mode_reviews_in_place(monkeypatch, tmp_path: Path) -> None:
    built = _stub_common(monkeypatch)
    repo = SimpleNamespace(name="Local", branch="feat", remote_url="")
    monkeypatch.setattr(cli, "detect_repos", lambda _p: [repo])

    target = cli._resolve_review_target(_args(tmp_path, pr=None), tmp_path / "arts")

    assert target.pr is None
    assert target.repo is repo
    assert target.label == "feat"
    assert built[0].mode == "local"


def test_resolve_pr_url_without_repo_arg_bootstraps(monkeypatch, tmp_path: Path) -> None:
    """`review --pr <url>` with no positional must not require a local checkout."""
    built = _stub_common(monkeypatch)
    calls: list = []
    monkeypatch.setattr(cli, "detect_repos", lambda p: calls.append(p) or [])
    pr = SimpleNamespace(pr_id=7, repo_name="Repo")
    monkeypatch.setattr(cli, "parse_pr_reference", lambda ref, hint: pr)
    monkeypatch.setattr(
        cli,
        "fetch_pr_metadata",
        lambda _pr: SimpleNamespace(
            source_branch="feature",
            target_branch="main",
            source_commit_sha="s" * 40,
            target_commit_sha="t" * 40,
        ),
    )
    monkeypatch.setattr(cli, "ado_clone_url", lambda _pr: "https://ado/Repo")

    target = cli._resolve_review_target(
        _args(tmp_path, pr="https://ado/_git/Repo/pullrequest/7", repo=None),
        tmp_path / "arts",
    )

    assert target.pr is pr
    assert built[0].mode == "pr"
    # Only the materialized workspace is probed — never a pre-workspace local repo.
    assert len(calls) == 1
    assert "arts" in calls[0]


def test_review_parser_repo_optional_with_pr_url() -> None:
    args = cli.build_parser().parse_args(["review", "--pr", "https://ado/_git/Repo/pullrequest/1"])
    assert args.repo is None
    assert args.pr.endswith("/1")


def test_review_parser_repo_positional_still_accepted() -> None:
    args = cli.build_parser().parse_args(["review", "some/repo"])
    assert args.repo == "some/repo"
    assert args.pr is None

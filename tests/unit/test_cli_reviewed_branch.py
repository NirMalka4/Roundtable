"""The branch under review has one derivation (:func:`cli._reviewed_branch`).

A ``--pr`` workspace is a detached worktree at the source SHA, so the repo detected
from it reports ``branch=None``. Every consumer must read the PR's source branch
instead, or the banner, the persisted subject and the publish anchor all say ``None``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from roundtable import cli


def _target(repo, metadata) -> cli._ReviewTarget:
    return cli._ReviewTarget(
        workspace=SimpleNamespace(path=Path("/w"), mode="branch"),
        repo=repo,
        pr=SimpleNamespace(pr_id=1, repo_name="R"),
        metadata=metadata,
        label="l",
        base_dir=Path("/a"),
        session_id="sid",
    )


def test_pr_review_reports_the_source_branch_not_the_detached_worktree():
    detached = SimpleNamespace(name="R", branch=None, remote_url="")
    metadata = SimpleNamespace(source_branch="user/me/feat", target_branch="main")

    assert _target(detached, metadata).branch == "user/me/feat"


def test_local_review_reports_the_checked_out_branch():
    repo = SimpleNamespace(name="R", branch="feat", remote_url="")

    assert _target(repo, None).branch == "feat"


def test_local_review_on_detached_head_falls_back_to_head():
    repo = SimpleNamespace(name="R", branch=None, remote_url="")

    assert _target(repo, None).branch == "HEAD"


def test_the_pr_fallback_repo_carries_no_branch_of_its_own():
    """The fallback descriptor must agree with detection, so the PR source branch
    stays the single answer regardless of which path produced the repo."""
    pr = SimpleNamespace(pr_id=7, repo_name="R", org="o", project="p", host="dev.azure.com")

    assert cli._pr_repo_fallback(pr, SimpleNamespace(source_branch="user/me/feat")).branch is None

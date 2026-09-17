"""Unit tests for ado.anchor — reviewed-iteration resolution + inline/general
classification."""

from __future__ import annotations

import pytest

from roundtable.ado.anchor import (
    AnchorDecision,
    IterationResolution,
    Relatedness,
    RelatednessStatus,
    assess_relatedness,
    classify,
    normalize_branch,
    normalize_repo_path,
    resolve_reviewed_iteration,
)
from roundtable.ado.pr_iterations import IterationRef
from roundtable.ado.publish import PublishableFinding


def _finding(**kw) -> PublishableFinding:
    base = {
        "id": "F-1",
        "title": "t",
        "description": "d",
        "severity": "High",
        "file_path": "svc/x.cs",
        "start_line": 10,
        "end_line": 12,
        "location_index": 0,
        "total_locations": 1,
        "additional_locations": (),
        "stable_hash": "h",
        "category": "blocking",
        "judge_category": None,
        "source_agents": ("Analyst",),
    }
    base.update(kw)
    return PublishableFinding(**base)


# ── path normalization (E7) ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a/svc/x.cs", "svc/x.cs"),
        ("b/svc/x.cs", "svc/x.cs"),
        ("/svc/x.cs", "svc/x.cs"),
        ("  svc/x.cs  ", "svc/x.cs"),
        ("svc/x.cs", "svc/x.cs"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_repo_path(raw, expected):
    assert normalize_repo_path(raw) == expected


# ── reviewed-iteration resolution ────────────────────────────────────────────
def test_resolve_iteration_match_returns_ordinal():
    iters = [
        IterationRef(ordinal=1, source_commit_sha="a" * 40),
        IterationRef(ordinal=2, source_commit_sha="b" * 40),
    ]
    res = resolve_reviewed_iteration(iters, "b" * 40)
    assert res == IterationResolution(2, None)


def test_resolve_iteration_match_is_case_insensitive_and_prefix_tolerant():
    iters = [IterationRef(ordinal=5, source_commit_sha="ABCDEF1234567890")]
    # persisted short SHA matches the full iteration SHA
    res = resolve_reviewed_iteration(iters, "abcdef123456")
    assert res.ordinal == 5 and res.reason is None


def test_resolve_iteration_no_match_gives_reason():
    iters = [IterationRef(ordinal=1, source_commit_sha="a" * 40)]
    res = resolve_reviewed_iteration(iters, "f" * 40)
    assert res.ordinal is None
    assert "no longer maps to a PR iteration" in res.reason


def test_resolve_iteration_falsy_sha_is_pre_provenance():
    res = resolve_reviewed_iteration([], None)
    assert res.ordinal is None
    assert "no reviewed commit" in res.reason


def test_resolve_iteration_empty_list_is_no_match():
    res = resolve_reviewed_iteration([], "a" * 40)
    assert res.ordinal is None and res.reason is not None


# ── branch normalization ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("refs/heads/user/x/foo", "user/x/foo"),
        ("user/x/foo", "user/x/foo"),
        ("  refs/heads/main  ", "main"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_branch(raw, expected):
    assert normalize_branch(raw) == expected


# ── relatedness verdict matrix ───────────────────────────────────────────────
def _iters(*shas):
    return [IterationRef(ordinal=i + 1, source_commit_sha=s) for i, s in enumerate(shas)]


def test_relatedness_anchored_when_sha_in_iterations():
    r = assess_relatedness(
        reviewed_sha="b" * 40,
        reviewed_branch="user/x/foo",
        iterations=_iters("a" * 40, "b" * 40),
        pr_source_ref=None,
        pr_display="PR #7",
    )
    assert r == Relatedness(RelatednessStatus.RELATED_ANCHORED, 2, None)


def test_relatedness_rolled_off_when_branch_matches():
    r = assess_relatedness(
        reviewed_sha="c" * 40,
        reviewed_branch="user/x/foo",
        iterations=_iters("a" * 40),
        pr_source_ref="refs/heads/user/x/foo",  # normalizes to the persisted short form
        pr_display="PR #7",
    )
    assert r.status is RelatednessStatus.RELATED_ROLLED_OFF
    assert r.ordinal is None and "rolled off" in r.reason


def test_relatedness_unrelated_when_branch_differs():
    r = assess_relatedness(
        reviewed_sha="c" * 40,
        reviewed_branch="user/x/foo",
        iterations=_iters("a" * 40),
        pr_source_ref="refs/heads/user/y/bar",
        pr_display="PR #7",
    )
    assert r.status is RelatednessStatus.UNRELATED
    assert "do not relate to this PR" in r.reason


def test_relatedness_unconfirmable_when_no_reviewed_sha():
    r = assess_relatedness(
        reviewed_sha=None,
        reviewed_branch="user/x/foo",
        iterations=_iters("a" * 40),
        pr_source_ref="refs/heads/user/y/bar",
        pr_display="PR #7",
    )
    assert r.status is RelatednessStatus.UNCONFIRMABLE
    assert "no reviewed commit" in r.reason


def test_relatedness_unconfirmable_when_iterations_unfetched():
    r = assess_relatedness(
        reviewed_sha="c" * 40,
        reviewed_branch="user/x/foo",
        iterations=None,
        pr_source_ref="refs/heads/user/x/foo",
        pr_display="PR #7",
    )
    assert r.status is RelatednessStatus.UNCONFIRMABLE
    assert "could not fetch PR #7 iterations" in r.reason


def test_relatedness_unconfirmable_when_branch_unknown():
    # SHA absent from iterations but no branch to compare ⇒ cannot prove wrong PR.
    r = assess_relatedness(
        reviewed_sha="c" * 40,
        reviewed_branch=None,
        iterations=_iters("a" * 40),
        pr_source_ref="refs/heads/user/x/foo",
        pr_display="PR #7",
    )
    assert r.status is RelatednessStatus.UNCONFIRMABLE
    assert "source branch could not be compared" in r.reason


def test_relatedness_unconfirmable_when_pr_ref_unfetched():
    r = assess_relatedness(
        reviewed_sha="c" * 40,
        reviewed_branch="user/x/foo",
        iterations=_iters("a" * 40),
        pr_source_ref=None,
        pr_display="PR #7",
    )
    assert r.status is RelatednessStatus.UNCONFIRMABLE
    assert "source branch could not be compared" in r.reason


# ── classification (inline vs general) ───────────────────────────────────────
def test_classify_inline_when_file_changed_and_line_valid():
    d = classify(_finding(), ["a/svc/x.cs"])
    assert d == AnchorDecision("inline", "svc/x.cs", 10, 12, None)


def test_classify_general_when_no_file():
    d = classify(_finding(file_path=None, start_line=None, end_line=None), ["svc/x.cs"])
    assert d.kind == "general" and "no file location" in d.downgrade_reason


def test_classify_general_when_file_not_in_changed_set():
    d = classify(_finding(), ["svc/other.cs"])
    assert d.kind == "general" and "changed set" in d.downgrade_reason


def test_classify_general_when_line_missing():
    d = classify(_finding(start_line=None), ["svc/x.cs"])
    assert d.kind == "general" and "start line" in d.downgrade_reason


def test_classify_end_line_defaults_to_start_when_invalid():
    d = classify(_finding(start_line=5, end_line=2), ["svc/x.cs"])
    assert d == AnchorDecision("inline", "svc/x.cs", 5, 5, None)

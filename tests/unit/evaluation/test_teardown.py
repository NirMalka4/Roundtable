"""Teardown reclaims evaluation scratch state without costing its evidence.

`eval-pr` mutates a shared repository on every run: two refs under
`refs/heads/roundtable/eval/<name>/` and a draft PR. Nothing reclaimed them, so five
evaluations of one PR left five drafts and ten refs behind, cleaned by hand twice.

The procedure is order-dependent, destructive and arcane — abandon before delete, a
40-zero sentinel for the deletion, a namespace check that must not be skipped — which is
why it is code and not agent prose. These tests pin the parts that are dangerous to get
wrong once, using injected operations so nothing here touches the network.
"""

from __future__ import annotations

import inspect

import pytest

from roundtable import evaluation
from roundtable.evaluation import EvaluationError


def _record(events: list[str], label: str, result: object = None) -> object:
    events.append(label)
    return result


# ── the order is load-bearing ───────────────────────────────────────────────────────────


def test_a_draft_is_abandoned_before_its_refs_are_deleted() -> None:
    """Azure DevOps refuses to delete a ref that still backs an active pull request, so a
    delete-first teardown leaves the draft orphaned and the ref alive."""
    events: list[str] = []

    evaluation.run_teardown(
        evaluation.teardown_targets(["earned-123"]),
        find_pull_requests=lambda _ref: (456,),
        abandon=lambda pr_id: _record(events, f"abandon:{pr_id}"),
        delete_ref=lambda ref: _record(events, f"delete:{ref.rsplit('/', 1)[-1]}", True),
    )

    assert events == ["abandon:456", "delete:source", "delete:base"]


# ── the namespace guard ─────────────────────────────────────────────────────────────────


def test_deleting_outside_the_evaluation_namespace_is_refused() -> None:
    """The guard is checked in code rather than trusted from the server-side ref filter
    that produced the name. Teardown deletes branches in a shared repository; the one
    invariant that makes that safe cannot depend on a caller having filtered correctly."""
    with pytest.raises(EvaluationError, match="refusing to delete"):
        evaluation.ensure_evaluation_ref("refs/heads/main")


def test_the_guard_runs_for_every_ref_a_sweep_enumerates() -> None:
    """A sweep's refs come from the server. Re-checking each one is what stops a widened
    filter, or a future change to `evaluation_refs`, from deleting real branches."""
    smuggled = evaluation.TeardownTarget(
        name="spoofed",
        source_ref="refs/heads/roundtable/eval/spoofed/source",
        target_ref="refs/heads/main",
    )

    with pytest.raises(EvaluationError, match="refusing to delete"):
        evaluation.run_teardown(
            [smuggled],
            find_pull_requests=lambda _ref: (),
            abandon=lambda _pr_id: None,
            delete_ref=lambda _ref: True,
        )


def test_a_sweep_recovers_names_only_from_evaluation_leaves() -> None:
    """`--all` enumerates refs, not names. Anything that is not a source/base leaf under
    the evaluation prefix contributes no name, so it is never a teardown target."""
    names = evaluation.sweep_names(
        [
            "refs/heads/roundtable/eval/alpha/source",
            "refs/heads/roundtable/eval/alpha/base",
            "refs/heads/roundtable/eval/beta/source",
            "refs/heads/roundtable/eval/gamma/scratch",
            "refs/heads/main",
        ]
    )

    assert names == ("alpha", "beta")


# ── teardown is re-run by nature ────────────────────────────────────────────────────────


def test_a_ref_that_is_already_gone_is_reported_not_raised() -> None:
    """Cleanup is repeated after partial runs and manual deletions; a missing ref is the
    state the caller asked for."""
    outcome = evaluation.run_teardown(
        evaluation.teardown_targets(["stale"]),
        find_pull_requests=lambda _ref: (),
        abandon=lambda _pr_id: None,
        delete_ref=lambda _ref: False,
    )

    assert outcome.deleted_refs == ()
    assert len(outcome.absent_refs) == 2


def test_dry_run_reports_the_full_plan_and_mutates_nothing() -> None:
    """`--all` carries the largest irreversible surface in this command, so the preview
    has to show every ref it would delete, not a count."""
    mutations: list[str] = []

    outcome = evaluation.run_teardown(
        evaluation.teardown_targets(["alpha", "beta"]),
        find_pull_requests=lambda _ref: (7,),
        abandon=lambda pr_id: mutations.append(f"abandon:{pr_id}"),
        delete_ref=lambda ref: mutations.append(ref) or True,
        dry_run=True,
    )

    assert mutations == []
    assert outcome.dry_run is True
    assert len(outcome.deleted_refs) == 4
    assert outcome.abandoned_pr_ids == (7, 7)


# ── the namespace is computed in exactly one place ──────────────────────────────────────


def test_teardown_targets_reuse_the_ref_builder_that_created_them() -> None:
    """`evaluation_refs` is the only place a name becomes ref names. Restating the pattern
    in teardown would let creation and reclamation drift apart silently."""
    source_ref, target_ref = evaluation.evaluation_refs("earned-123")
    target = evaluation.teardown_targets(["earned-123"])[0]

    assert (target.source_ref, target.target_ref) == (source_ref, target_ref)
    assert source_ref.startswith(evaluation.EVALUATION_REF_PREFIX)


# ── deletion is slow, and a timeout is not a failure to undo ────────────────────────────


def test_ref_deletion_waits_far_longer_than_a_read() -> None:
    """Measured at 83s on a large Azure DevOps repository, which failed three consecutive
    attempts against the 30s default used for reads. The request had in fact been applied
    server-side each time — a client that gives up early reports a lie."""
    assert evaluation.REF_DELETE_TIMEOUT >= 180.0

    signature = inspect.signature(evaluation.delete_evaluation_ref)
    assert signature.parameters["timeout"].default == evaluation.REF_DELETE_TIMEOUT

"""ado.unpublish_flow: the deterministic *unpublish* pipeline — the dual of publish.

Given the same parity-checked :class:`~roundtable.delivery.publishable.PublishableResult`
and persisted ``subject`` that
``publish`` used, this removes the comment threads a prior ``publish`` posted to the
PR. It is intentionally the simpler dual:

* **Watermark-scoped, forge-proof targeting.** Each published comment embeds a
  ``<!-- Roundtable:<finding_id>:H=<hash> -->`` watermark (a legacy
  ``InspectorX-CLI:`` tag from a pre-rebrand release is still recognized).
  Unpublish recomputes this session's finding hashes and matches summaries by
  session identity. Identity matching retracts legacy duplicate summaries whose
  content changed across publication views without touching another session.
* **Delete only *our* comment.** A match resolves to the *specific* comment id that
  holds the watermark; the DELETE targets that comment alone, leaving any human
  replies (and the thread shell) intact.
* **Idempotent + retry-safe.** DELETE is idempotent, so a re-run after a partial
  unpublish just sees ``absent`` (HTTP 404) and never fails on that account. There
  is no close/resolve fallback and no permission fallback — a forbidden or errored
  delete is reported ``failed``.

Like publish, the PR resolution and the thread poster are injected so the whole
flow is unit-testable with no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from roundtable.delivery import PublishableResult
from roundtable.inputs import PrReference
from roundtable.review import OverlayKey as K
from roundtable.settings import relative_artifact_path

from .anchor import RelatednessStatus
from .pr_iterations import IterationRef, fetch_pr_iterations, fetch_pr_source_ref
from .pr_labels import PrLabelClient
from .pr_threads import PostResult, PrThreadPoster, summary_matches_session
from .publish_flow import assess_relatedness_for_pr, resolve_publish_pr

_SUMMARY_ID = "__summary__"


@dataclass(frozen=True)
class UnpublishOptions:
    dry_run: bool = False
    pr_override: str | None = None
    out_path: str | None = None


@dataclass(frozen=True)
class UnpublishResult:
    """Per-comment outcome of an unpublish attempt."""

    finding_id: str  # the label the watermark carried (or ``__summary__``)
    thread_id: int
    comment_id: int
    status: str  # 'removed' | 'absent' | 'failed'


@dataclass
class UnpublishReport:
    """Per-run counters + per-comment results."""

    targets: int  # distinct session watermark hashes we looked for
    matched: int  # watermarked PR comments that matched a target hash
    results: list[UnpublishResult] = field(default_factory=list)
    dry_run_path: str | None = None
    relatedness_status: str | None = None
    warnings: list[str] = field(default_factory=list)
    labels_removed: int = 0
    summary_result: PostResult | None = None
    metadata_retained: bool = False

    @property
    def removed(self) -> int:
        return sum(1 for r in self.results if r.status == "removed")

    @property
    def absent(self) -> int:
        return sum(1 for r in self.results if r.status == "absent")

    @property
    def failed(self) -> int:
        delete_failures = sum(1 for r in self.results if r.status == "failed")
        summary_failure = int(
            self.summary_result is not None
            and self.summary_result.status in {"failed", "ambiguous"}
        )
        return delete_failures + summary_failure

    @property
    def ok(self) -> bool:
        """True when nothing failed (already-absent comments are a clean no-op)."""
        return self.failed == 0


def _target_hashes(result: PublishableResult) -> dict[str, str]:
    """The ``{watermark_hash: label}`` set this session would have published.

    Every finding contributes its ``stable_hash`` regardless of severity, and
    the executive summary contributes ``summary_stable_hash(session_id,
    all_findings)`` — mirroring ``run_publish``, whose summary now spans the full
    result. Publish only *inlines* the medium+ view, so the non-inlined low/info
    finding hashes are a harmless superset here (they simply match no PR comment
    and are reported ``absent``). This removes the old ``--min-severity`` coupling
    that made unpublish targeting depend on a flag the user had to remember.
    """
    return {finding.stable_hash: finding.id for finding in result.all_findings}


def _matching_comments(
    result: PublishableResult,
    targets: dict[str, str],
    watermarked: list[tuple[int, int, str, str, str]],
    session_references: tuple[str, ...],
) -> list[UnpublishResult]:
    """Match this session's exact finding hashes and summary identity."""
    matches: list[UnpublishResult] = []
    for thread_id, comment_id, identity, stable_hash, content in watermarked:
        label = targets.get(stable_hash)
        if label is None and any(
            summary_matches_session(identity, content, reference)
            for reference in session_references
        ):
            label = _SUMMARY_ID
        if label is not None:
            matches.append(UnpublishResult(label, thread_id, comment_id, "matched"))
    return matches


def _session_references(
    result: PublishableResult,
    session_dir: str,
    artifacts_root: str | Path | None,
) -> tuple[str, ...]:
    references = [result.session_id]
    roots = (artifacts_root, Path(session_dir).parent)
    for root in roots:
        try:
            references.append(relative_artifact_path(session_dir, root))
        except ValueError:
            continue
    return tuple(dict.fromkeys(references))


def _write_dry_run(
    matches: list[UnpublishResult],
    targets: int,
    relatedness_status: str | None,
    relatedness_reason: str | None,
    metadata_available: bool,
    out_path: Path,
) -> None:
    payload = {
        "targets": targets,
        "matched": len(matches),
        "comments": [
            {
                "findingId": m.finding_id,
                "threadId": m.thread_id,
                "commentId": m.comment_id,
            }
            for m in matches
        ],
        "relatedness": {"status": relatedness_status, "reason": relatedness_reason},
        "retained": {
            "metadataSummary": metadata_available,
            "currentStateLabel": True,
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _default_poster(pr: PrReference) -> PrThreadPoster:
    return PrThreadPoster(pr)


def run_unpublish(
    result: PublishableResult,
    subject: dict | None,
    options: UnpublishOptions,
    *,
    session_dir: str,
    artifacts_root: str | Path | None = None,
    remote_url: str | None = None,
    poster_factory: Callable[[PrReference], PrThreadPoster] | None = None,
    iteration_fetcher: Callable[[PrReference], list[IterationRef]] | None = None,
    pr_source_ref_fetcher: Callable[[PrReference], str | None] | None = None,
    label_client_factory: Callable[[PrReference], PrLabelClient] | None = None,
) -> UnpublishReport:
    """Execute the unpublish pipeline: delete this session's watermarked comments."""
    pr = resolve_publish_pr(subject, options.pr_override, remote_url=remote_url)
    targets = _target_hashes(result)

    # Symmetric with publish: never touch a PR the session is *proven*
    # unrelated to — even though the watermark scope makes a wrong-PR delete a
    # harmless no-op, blocking gives a clear error instead of a silent "matched 0".
    relatedness = assess_relatedness_for_pr(
        pr,
        (subject or {}).get(K.SUBJECT_SOURCE_SHA),
        (subject or {}).get(K.SUBJECT_SOURCE_BRANCH),
        iteration_fetcher or fetch_pr_iterations,
        pr_source_ref_fetcher or fetch_pr_source_ref,
    )
    if relatedness.status is RelatednessStatus.UNRELATED and not options.dry_run:
        raise ValueError(relatedness.reason)

    poster = (poster_factory or _default_poster)(pr)
    watermarked = poster.find_watermarked_comment_details()
    session_references = _session_references(result, session_dir, artifacts_root)
    matches = _matching_comments(result, targets, watermarked, session_references)
    report = UnpublishReport(targets=len(targets) + 1, matched=len(matches))
    report.relatedness_status = relatedness.status.value
    if relatedness.status is not RelatednessStatus.RELATED_ANCHORED and relatedness.reason:
        report.warnings.append(relatedness.reason)
    if options.dry_run:
        out = Path(options.out_path) if options.out_path else Path(session_dir) / "unpublish.json"
        _write_dry_run(
            matches,
            len(targets) + 1,
            relatedness.status.value,
            relatedness.reason,
            False,
            out,
        )
        report.dry_run_path = str(out)
        return report

    for m in matches:
        status = poster.delete_comment(m.thread_id, m.comment_id)
        report.results.append(UnpublishResult(m.finding_id, m.thread_id, m.comment_id, status))

    return report

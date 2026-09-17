"""ado.publish_flow: the deterministic publish pipeline, decoupled from argparse.

Given an already-projected, parity-checked
:class:`~roundtable.delivery.publishable.PublishableResult` and the session's
persisted ``subject`` provenance, this turns findings into anchored, rendered comments
and either writes them (``dry_run``) or posts them to the PR. The network
touchpoints — the PR-iterations fetch (to resolve the reviewed iteration ordinal),
the iteration-changes fetch (to resolve each file's ``changeTrackingId``), and the
thread poster — are injected so the whole flow is unit-testable with no
network (E11).

Inline comments carry the reviewed **iteration ordinal** so ADO auto-tracks them
forward to the PR head. When that ordinal can't be resolved (pre-provenance
session, force-pushed history, or an unreachable/​unauthenticated iterations
fetch), the affected comments never fail and never skip — they downgrade to
general threads and the reason is surfaced as a terminal warning.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from roundtable.adoption import encode_label, installation_source
from roundtable.delivery import Commenter, PublishableResult
from roundtable.graph import Configuration
from roundtable.inputs import PrReference, parse_pr_reference
from roundtable.review import OverlayKey as K
from roundtable.settings import relative_artifact_path

from .anchor import (
    AnchorDecision,
    IterationResolution,
    Relatedness,
    RelatednessStatus,
    assess_relatedness,
    classify,
    sha_matches,
)
from .comment_format import (
    DefaultCommenter,
    min_severity_view,
    severity_ordered,
    with_publication_footer,
)
from .pr_iterations import (
    IterationRef,
    fetch_pr_iteration_changes,
    fetch_pr_iterations,
    fetch_pr_source_ref,
)
from .pr_labels import PrLabelClient, stamp_adoption_label
from .pr_threads import PostResult, PreparedComment, PrThreadPoster
from .publish import PublishableFinding
from .review_record import append_metadata, load_review_record


@dataclass(frozen=True)
class PublishOptions:
    min_severity: str | None = None
    dry_run: bool = False
    out_path: str | None = None
    pr_override: str | None = None
    allow_config_drift: bool = False


@dataclass
class PublishReport:
    """Per-run counters + per-finding results (E6)."""

    total: int
    eligible: int
    skipped_by_threshold: int
    inline: int
    general: int
    results: list[PostResult] = field(default_factory=list)
    dry_run_path: str | None = None
    summary_result: PostResult | None = None
    anchor_warnings: list[str] = field(default_factory=list)
    relatedness_status: str | None = None
    already_published: bool = False
    version_label: str | None = None
    label_action: str = "skipped"  # added | present | replaced | skipped | failed | dry-run

    @property
    def posted(self) -> int:
        return sum(1 for r in self.results if r.status == "posted")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def failed(self) -> int:
        finding_failures = sum(1 for r in self.results if r.status in ("failed", "ambiguous"))
        summary_failure = int(
            self.summary_result is not None
            and self.summary_result.status in ("failed", "ambiguous")
        )
        return finding_failures + summary_failure

    @property
    def summary_status(self) -> str:
        return self.summary_result.status if self.summary_result is not None else "not-posted"

    @property
    def ok(self) -> bool:
        """True when nothing failed or was left ambiguous (E5)."""
        return self.failed == 0


def resolve_publish_pr(
    subject: dict | None,
    override: str | None,
    *,
    remote_url: str | None = None,
) -> PrReference:
    """Resolve the target PR: the persisted ``subject`` provenance, optionally overridden.

    A ``--pr`` override is identity-checked against the persisted provenance —
    org/project/repo must match — so a publish can never be redirected onto a
    different repository than the one that was reviewed (E10).
    """
    persisted = _pr_from_subject(subject)
    if override:
        url_for_override = remote_url or (subject or {}).get(K.SUBJECT_REMOTE_URL)
        chosen = parse_pr_reference(override, url_for_override)
        if persisted is not None and not _same_repo(chosen, persisted):
            raise ValueError(
                "--pr override targets a different repository than the reviewed "
                f"session ({chosen.org}/{chosen.project}/{chosen.repo_name} vs "
                f"{persisted.org}/{persisted.project}/{persisted.repo_name}); refusing."
            )
        return chosen
    if persisted is None:
        raise ValueError(
            "This session has no persisted PR provenance (it was not a --pr review). "
            "Pass --pr <id-or-url> to name the target pull request."
        )
    return persisted


def _pr_from_subject(subject: dict | None) -> PrReference | None:
    """Reconstruct a :class:`PrReference` from the ``subject`` block.

    Returns ``None`` for a non-PR (branch) review or a pre-provenance session that
    lacks the ``prId`` / ``remoteUrl`` coordinates.
    """
    if not subject:
        return None
    pr_id = subject.get(K.SUBJECT_PR_ID)
    remote_url = subject.get(K.SUBJECT_REMOTE_URL)
    if pr_id is None or not remote_url:
        return None
    try:
        ref = parse_pr_reference(str(pr_id), remote_url)
    except ValueError:
        return None
    title = subject.get(K.SUBJECT_PR_TITLE)
    if title:
        ref.pr_title = title
    return ref


def _same_repo(a: PrReference, b: PrReference) -> bool:
    return (
        a.org.lower() == b.org.lower()
        and a.project.lower() == b.project.lower()
        and a.repo_name.lower() == b.repo_name.lower()
    )


def build_prepared_comments(
    findings: Sequence[PublishableFinding],
    changed_files: Sequence[str],
    session_id: str,
    resolution: IterationResolution,
    configuration: Configuration,
    session_reference: str,
    change_tracking_ids: dict[str, int] | None = None,
    commenter: Commenter | None = None,
) -> tuple[list[PreparedComment], int, int, list[str]]:
    """Classify + render each finding into a :class:`PreparedComment`.

    An inline-eligible finding keeps its anchor and is tagged with the reviewed
    iteration ordinal (``resolution.ordinal``) only when that ordinal resolved;
    otherwise it downgrades to a general thread. Every downgrade reason is
    aggregated with a count for the terminal warning.

    ``change_tracking_ids`` maps each changed file's normalized path to its
    per-iteration-stable ADO ``changeTrackingId``; the matching id is attached to
    an inline comment so ADO tracks the file forward (including renames). A missing
    entry is fine — the comment still tracks on ``iterationContext`` alone.

    Returns ``(prepared, inline_count, general_count, warnings)`` where ``warnings``
    is an ordered list of ``"<reason> (N comment(s) posted as general threads)"``.
    """
    ctids = change_tracking_ids or {}
    render = (commenter or DefaultCommenter(configuration)).render_thread
    prepared: list[PreparedComment] = []
    inline = general = 0
    reason_counts: dict[str, int] = {}

    def _bump(reason: str) -> None:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    for finding in severity_ordered(findings, configuration):
        decision = classify(finding, changed_files)
        iteration: int | None = None
        if decision.kind == "inline":
            if resolution.ordinal is not None:
                iteration = resolution.ordinal
            else:
                _bump(resolution.reason or "reviewed iteration could not be resolved")
                decision = AnchorDecision(
                    "general",
                    decision.file_path,
                    None,
                    None,
                    "reviewed iteration unavailable — see terminal warning",
                )
        elif decision.downgrade_reason:
            _bump(decision.downgrade_reason)

        if decision.kind == "inline":
            inline += 1
        else:
            general += 1

        is_inline = decision.kind == "inline"
        prepared.append(
            PreparedComment(
                finding_id=finding.id,
                stable_hash=finding.stable_hash,
                content=with_publication_footer(
                    render(finding, decision, session_id=session_id),
                    configuration,
                    session_reference,
                    source_agents=finding.source_agents,
                ),
                file_path=decision.file_path if is_inline else None,
                start_line=decision.start_line if is_inline else None,
                end_line=decision.end_line if is_inline else None,
                iteration=iteration,
                change_tracking_id=(
                    ctids.get(decision.file_path)
                    if is_inline and decision.file_path is not None and iteration is not None
                    else None
                ),
            )
        )

    warnings = [
        f"{reason} ({count} comment(s) posted as general threads)"
        for reason, count in reason_counts.items()
    ]
    return prepared, inline, general, warnings


def _write_dry_run(
    prepared: Sequence[PreparedComment],
    summary: str,
    warnings: Sequence[str],
    relatedness: Relatedness,
    version_label: str | None,
    out_path: Path,
) -> None:
    payload = {
        "threads": [
            {
                "findingId": c.finding_id,
                "stableHash": c.stable_hash,
                "kind": "inline" if c.file_path else "general",
                "filePath": c.file_path,
                "startLine": c.start_line,
                "endLine": c.end_line,
                "iteration": c.iteration,
                "changeTrackingId": c.change_tracking_id,
                "content": c.content,
            }
            for c in prepared
        ],
        "summary": summary,
        "anchorWarnings": list(warnings),
        "relatedness": {"status": relatedness.status.value, "reason": relatedness.reason},
        "versionLabel": version_label,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def assess_relatedness_for_pr(
    pr: PrReference,
    reviewed_sha: str | None,
    reviewed_branch: str | None,
    iteration_fetcher: Callable[[PrReference], list[IterationRef]],
    pr_source_ref_fetcher: Callable[[PrReference], str | None],
) -> Relatedness:
    """Best-effort relatedness verdict for the target PR (lazy two-step fetch).

    Fetches the PR's iterations first (needed anyway for anchoring). The PR's
    source branch is fetched **only** when the reviewed commit is absent from the
    iterations — the sole case where it disambiguates a force-push (same branch ⇒
    downgrade) from a wrong PR (different branch ⇒ block). The happy path therefore
    stays at one network call.

    Every fetch is swallowed into an ``UNCONFIRMABLE`` verdict rather than raising:
    the guard blocks only on *positive* proof of a wrong PR (see
    :func:`roundtable.ado.anchor.assess_relatedness`).
    """
    pr_display = f"PR #{pr.pr_id}"
    if not reviewed_sha:
        return assess_relatedness(
            reviewed_sha=None,
            reviewed_branch=reviewed_branch,
            iterations=None,
            pr_source_ref=None,
            pr_display=pr_display,
        )
    try:
        iterations: list[IterationRef] | None = iteration_fetcher(pr)
    except Exception:
        iterations = None

    pr_source_ref: str | None = None
    needs_branch = iterations is not None and not any(
        sha_matches(it.source_commit_sha, reviewed_sha.strip().lower()) for it in iterations
    )
    if needs_branch:
        try:
            pr_source_ref = pr_source_ref_fetcher(pr)
        except Exception:
            pr_source_ref = None

    return assess_relatedness(
        reviewed_sha=reviewed_sha,
        reviewed_branch=reviewed_branch,
        iterations=iterations,
        pr_source_ref=pr_source_ref,
        pr_display=pr_display,
    )


def _resolve_change_tracking_ids_for_pr(
    pr: PrReference,
    ordinal: int | None,
    changes_fetcher: Callable[[PrReference, int], dict[str, int]],
) -> dict[str, int]:
    """Best-effort ``{path: changeTrackingId}`` for the reviewed iteration.

    Only fetched when an iteration ordinal resolved. Any failure (offline / no
    credentials / HTTP error) yields an empty map — inline comments still post and
    track on ``iterationContext`` alone, so a missing id never fails or downgrades
    a comment.
    """
    if ordinal is None:
        return {}
    try:
        return changes_fetcher(pr, ordinal)
    except Exception:
        return {}


def run_publish(
    result: PublishableResult,
    subject: dict | None,
    diff_stat: dict | None,
    options: PublishOptions,
    configuration: Configuration,
    *,
    session_dir: str,
    artifacts_root: str | Path | None = None,
    remote_url: str | None = None,
    poster_factory: Callable[[PrReference], PrThreadPoster] | None = None,
    iteration_fetcher: Callable[[PrReference], list[IterationRef]] | None = None,
    changes_fetcher: Callable[[PrReference, int], dict[str, int]] | None = None,
    pr_source_ref_fetcher: Callable[[PrReference], str | None] | None = None,
    label_client_factory: Callable[[PrReference], PrLabelClient] | None = None,
) -> PublishReport:
    """Execute the publish pipeline against a parity-checked result."""
    if not session_dir or not session_dir.strip():
        raise ValueError("Publishing requires a non-empty local session directory.")
    session_reference = relative_artifact_path(session_dir, artifacts_root)
    pr = resolve_publish_pr(subject, options.pr_override, remote_url=remote_url)
    reviewed_sha = (subject or {}).get(K.SUBJECT_SOURCE_SHA)
    reviewed_branch = (subject or {}).get(K.SUBJECT_SOURCE_BRANCH)
    changed_files = list((diff_stat or {}).get("changedFiles") or [])

    all_findings = result.all_findings
    # The configuration/CLI severity floor gates the per-finding comment threads:
    # a below-floor finding is dropped from EVERY thread (inline and general), not
    # just denied an inline anchor. Nothing is hidden — the executive summary below
    # is built over the full plan, so low/info findings still surface there.
    view = min_severity_view(all_findings, options.min_severity, configuration)
    report = PublishReport(
        total=len(all_findings),
        eligible=len(view),
        skipped_by_threshold=len(all_findings) - len(view),
        inline=0,
        general=0,
    )
    if not all_findings:
        return report

    # Verify the session's review relates to this PR, and (best-effort) resolve the
    # reviewed iteration up front so BOTH the dry-run preview and the live post
    # carry the true iterationContext / downgrade.
    relatedness = assess_relatedness_for_pr(
        pr,
        reviewed_sha,
        reviewed_branch,
        iteration_fetcher or fetch_pr_iterations,
        pr_source_ref_fetcher or fetch_pr_source_ref,
    )
    report.relatedness_status = relatedness.status.value
    # Confirmed wrong PR: refuse to post (no override). A dry-run still surfaces the
    # verdict (below) and writes the preview rather than failing.
    if relatedness.status is RelatednessStatus.UNRELATED and not options.dry_run:
        raise ValueError(relatedness.reason)

    # Feed anchoring: ordinal set only when RELATED_ANCHORED; otherwise the
    # relatedness reason drives the per-finding downgrade warning.
    resolution = IterationResolution(
        relatedness.ordinal,
        None if relatedness.ordinal is not None else relatedness.reason,
    )
    change_tracking_ids = _resolve_change_tracking_ids_for_pr(
        pr, resolution.ordinal, changes_fetcher or fetch_pr_iteration_changes
    )

    commenter = result.commenter or DefaultCommenter(configuration)
    prepared, report.inline, report.general, report.anchor_warnings = build_prepared_comments(
        view,
        changed_files,
        session_reference,
        resolution,
        configuration,
        session_reference,
        change_tracking_ids,
        commenter,
    )
    summary = commenter.render_summary(
        session_reference, result.verdict, all_findings, published=view
    )
    summary = with_publication_footer(summary, configuration, session_reference)
    summary = _scope_summary_watermark(summary, session_reference)

    from .. import __version__

    adoption_record = load_review_record(session_dir)
    if adoption_record is not None:
        summary = append_metadata(
            summary,
            adoption_record,
            session_reference=session_reference,
        )
        report.version_label = encode_label(
            adoption_record.tool_version,
            adoption_record.configuration_name,
            adoption_record.installation_source,
            graph_config_sha=adoption_record.graph_config_sha,
        )
    else:
        report.version_label = encode_label(
            __version__,
            configuration.name,
            installation_source(),
            graph_config_sha=configuration.fingerprint,
        )

    if options.dry_run:
        out = Path(options.out_path) if options.out_path else Path(session_dir) / "threads.json"
        report.label_action = "dry-run" if report.version_label else "skipped"
        _write_dry_run(
            prepared, summary, report.anchor_warnings, relatedness, report.version_label, out
        )
        report.dry_run_path = str(out)
        return report

    # ADO displays newer threads first. Create findings from lowest to highest
    # severity, then create the summary last so the UI reads summary, high, medium, low.
    poster = (poster_factory or _default_poster)(pr)
    summary_comment = PreparedComment(
        finding_id="__summary__",
        stable_hash=_summary_hash_from(summary),
        content=summary,
        status=4,
    )

    if prepared:
        report.results = poster.publish(list(reversed(prepared)))
    summary_results = poster.publish_summary(
        summary_comment,
        summary_identity=session_reference,
        legacy_summary_identities=(result.session_id,),
        force_recreate=any(item.status == "posted" for item in report.results),
    )
    report.summary_result = summary_results[0] if summary_results else None
    report.already_published = (
        report.summary_result is not None
        and report.summary_result.status == "skipped"
        and all(item.status == "skipped" for item in report.results)
    )

    if report.summary_result is not None and report.summary_result.status in {
        "posted",
        "skipped",
    }:
        report.version_label, report.label_action = stamp_adoption_label(
            pr,
            report.version_label,
            client_factory=label_client_factory,
        )
    else:
        report.label_action = "failed"
    return report


def _summary_hash_from(summary: str) -> str:
    from .pr_threads import parse_watermark_hashes

    hashes = parse_watermark_hashes(summary)
    return next(iter(hashes)) if hashes else ""


def _scope_summary_watermark(summary: str, session_id: str) -> str:
    """Give the generic summary thread a stable session identity."""
    marker = "<!-- Roundtable:__summary__:H="
    scoped = f"<!-- Roundtable:__summary__:{session_id}:H="
    return summary.replace(marker, scoped, 1)


def _default_poster(pr: PrReference) -> PrThreadPoster:
    return PrThreadPoster(pr)

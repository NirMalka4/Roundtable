"""ado.sink: the Azure DevOps output sink — the ``azure_devops`` :class:`Sink` impl.

Adapts a persisted review session into ADO PR comments. This is the sole concrete
sink behind the destination-agnostic seam (``output.sink``); it owns the whole
"talk to Azure DevOps" pipeline — reading the session dir, extracting the publish
plan, resolving the PR, posting (or retracting) threads, and printing the
operational report — and hands the caller only a neutral :class:`SinkOutcome`.

The pipeline bodies here are lifted verbatim from the former ``cli`` publish /
unpublish helpers; the only change is returning a ``SinkOutcome`` (ok + exit code)
instead of a raw exit int, so the CLI composition root dispatches through
``get_sink(config.sink)`` and stays ADO-free. Any sink-agnostic pre-gate is
intentionally NOT here — it stays in the caller (it verifies the *review*, not the
destination).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from roundtable.decision import EXIT_ABORTED, EXIT_CLEAN, EXIT_ERROR
from roundtable.delivery import (
    PublishOutcome,
    PublishRequest,
    RetractRequest,
    get_projector,
)
from roundtable.review import OverlayKey

from .publish_flow import PublishOptions, run_publish
from .unpublish_flow import UnpublishOptions, run_unpublish


def _session_results_from_trace(session_dir: str) -> dict:
    """Reconstruct the per-agent map for the publish extractor.

    Prefers the normalized ``raw_results.json`` for responses; falls back
    to the slim trace's per-agent ``response`` field (persistence/trace.py).

    The trace's failure facts (``valid``/``gate``/``attempts``/``attemptsDetail``) are
    merged in either way. ``raw_results.json`` carries only ``response``, so reading it
    alone makes an agent that timed out indistinguishable from one that said nothing —
    and publishing is the surface where that distinction matters most, because a human
    reads the PR comment and infers full coverage from it.
    """
    trace_agents = _trace_agent_records(session_dir)
    raw_path = Path(session_dir) / "raw_results.json"
    if raw_path.exists():
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                key: {**trace_agents.get(key, {}), **value} if isinstance(value, dict) else value
                for key, value in data.items()
            }
    return _trace_agent_records(session_dir, tolerant=False)


_TRACE_FAILURE_FIELDS = ("valid", "gate", "attempts", "attemptsDetail")


def _trace_agent_records(session_dir: str, *, tolerant: bool = True) -> dict:
    """``{agent: {response, valid, gate, attempts, attemptsDetail}}`` from the trace.

    ``tolerant`` when the trace is only enriching a ``raw_results.json`` that already
    carries the responses: a session must still publish without it. Strict when the
    trace IS the source, so an unreadable one still blocks loudly rather than
    presenting as an empty run.
    """
    trace_path = Path(session_dir) / "trace.json"
    try:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if not tolerant:
            raise
        return {}
    results: dict = {}
    for agent in trace.get("agents", []):
        if not isinstance(agent, dict) or not isinstance(agent.get("agent"), str):
            continue
        record = {"response": agent.get("response", "")}
        record.update({f: agent[f] for f in _TRACE_FAILURE_FIELDS if f in agent})
        results[agent["agent"]] = record
    return results


def _read_subject(session_dir: str) -> tuple[dict | None, dict | None]:
    """Read the persisted ``subject`` + ``diffStat`` blocks from ``trace.json``.

    Returns ``(subject, diff_stat)``; either is ``None`` when absent (a
    pre-provenance session, or a review that predates the canonical subject block).
    """
    try:
        trace = json.loads((Path(session_dir) / "trace.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    subject = trace.get(OverlayKey.SUBJECT)
    diff_stat = trace.get(OverlayKey.DIFF_STAT)
    return (
        subject if isinstance(subject, dict) else None,
        diff_stat if isinstance(diff_stat, dict) else None,
    )


def _recorded_artifacts_root(session_dir: str) -> str | None:
    """Read the effective artifacts root used when the session was created."""
    try:
        trace = json.loads((Path(session_dir) / "trace.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    provenance = trace.get(OverlayKey.PROVENANCE)
    effective = provenance.get("effectiveConfig") if isinstance(provenance, dict) else None
    if not isinstance(effective, list):
        return None
    for item in effective:
        if isinstance(item, dict) and item.get("name") == "artifacts_dir":
            value = item.get("value")
            return value if isinstance(value, str) and value else None
    return None


class AzureDevOpsPublisher:
    """Publish/unpublish a review session's findings to its Azure DevOps PR."""

    name = "azure_devops"

    def publish(
        self,
        request: PublishRequest | str,
        options: object | None = None,
    ) -> PublishOutcome:
        """Post one inline thread per finding + an executive-summary thread.

        Pipeline: read the session
        → project the terminal output → topology abort gate → resolve target PR +
        reviewed iteration → render/anchor → live-post or ``--dry-run`` preview →
        report.
        """
        session_dir, options = _publish_arguments(request, options)
        from roundtable.graph import get_configuration

        try:
            session_results = _session_results_from_trace(session_dir)
        except (OSError, ValueError) as err:
            print(f"[publish] BLOCKED — could not read trace.json: {err}", file=sys.stderr)
            return PublishOutcome(ok=False, exit_code=EXIT_ABORTED)

        configuration = get_configuration()
        plan = get_projector(configuration.projector).project(
            session_results, session_dir_path=session_dir
        )
        if plan is None:
            print(
                "[publish] no publishable plan (Judge missing or unparseable); nothing to publish.",
                file=sys.stderr,
            )
            return PublishOutcome(ok=True, exit_code=EXIT_CLEAN, message="nothing to publish")

        if plan.abort_reason is not None:
            print(f"[publish] ABORTED — {plan.abort_reason}", file=sys.stderr)
            return PublishOutcome(ok=False, exit_code=EXIT_ABORTED, message=plan.abort_reason)

        if plan.log_summary:
            print(f"[publish] {plan.log_summary}", file=sys.stderr)

        subject, diff_stat = _read_subject(session_dir)
        try:
            report = run_publish(
                plan,
                subject,
                diff_stat,
                options,
                configuration,
                session_dir=session_dir,
                artifacts_root=_recorded_artifacts_root(session_dir),
            )
        except (ValueError, RuntimeError) as err:
            print(f"[publish] BLOCKED — {err}", file=sys.stderr)
            return PublishOutcome(ok=False, exit_code=EXIT_ABORTED, message=str(err))

        for warning in report.anchor_warnings:
            print(f"[publish] WARNING — {warning}", file=sys.stderr)

        print(
            f"[publish] total={report.total} eligible={report.eligible} "
            f"skipped-by-threshold={report.skipped_by_threshold} "
            f"inline={report.inline} general={report.general}",
            file=sys.stderr,
        )
        if report.dry_run_path is not None:
            print(
                f"[publish] dry-run: wrote {report.eligible} thread(s) to {report.dry_run_path}",
                file=sys.stderr,
            )
            if report.version_label is not None:
                print(
                    f"[publish] dry-run: would tag PR with label {report.version_label}",
                    file=sys.stderr,
                )
            return PublishOutcome(ok=True, exit_code=EXIT_CLEAN, message="dry-run")

        for result in report.results:
            print(
                f"[publish]   {result.finding_id}: {result.status}"
                + (f" ({result.detail})" if result.detail else ""),
                file=sys.stderr,
            )
        summary_result = getattr(report, "summary_result", None)
        if summary_result is not None:
            result = summary_result
            print(
                f"[publish] summary: {result.status}"
                + (f" ({result.detail})" if result.detail else ""),
                file=sys.stderr,
            )
        if report.already_published:
            print(
                "[publish] ALREADY PUBLISHED — every comment for this session is already "
                "on the PR; nothing new to post. Run `roundtable unpublish` first to "
                "re-post.",
                file=sys.stderr,
            )
        if report.version_label is not None:
            print(
                f"[publish] label: {report.version_label} ({report.label_action})",
                file=sys.stderr,
            )
        print(
            f"[publish] posted={report.posted} skipped(dedup)={report.skipped} "
            f"failed={report.failed}",
            file=sys.stderr,
        )
        return PublishOutcome(ok=report.ok, exit_code=EXIT_CLEAN if report.ok else EXIT_ERROR)

    def retract(self, request: RetractRequest) -> PublishOutcome:
        return self.unpublish(str(request.session_dir), request.options)

    def unpublish(self, session_dir: str, options: object | None) -> PublishOutcome:
        """Delete *only* the watermarked comments this session posted (publish's dual).

        No parity gate — a session must still be
        retractable. ``--dry-run`` previews matches without deleting.
        """
        assert isinstance(options, UnpublishOptions)
        from roundtable.graph import get_configuration

        try:
            session_results = _session_results_from_trace(session_dir)
        except (OSError, ValueError) as err:
            print(f"[unpublish] BLOCKED — could not read trace.json: {err}", file=sys.stderr)
            return PublishOutcome(ok=False, exit_code=EXIT_ABORTED)

        plan = get_projector(get_configuration().projector).project(
            session_results, session_dir_path=session_dir
        )
        if plan is None:
            print(
                "[unpublish] no publishable plan (Judge missing or unparseable); "
                "nothing could have been published.",
                file=sys.stderr,
            )
            return PublishOutcome(ok=True, exit_code=EXIT_CLEAN, message="nothing to unpublish")

        subject, _diff_stat = _read_subject(session_dir)
        try:
            report = run_unpublish(
                plan,
                subject,
                options,
                session_dir=session_dir,
                artifacts_root=_recorded_artifacts_root(session_dir),
            )
        except (ValueError, RuntimeError) as err:
            print(f"[unpublish] BLOCKED — {err}", file=sys.stderr)
            return PublishOutcome(ok=False, exit_code=EXIT_ABORTED, message=str(err))

        for warning in report.warnings:
            print(f"[unpublish] WARNING — {warning}", file=sys.stderr)

        print(
            f"[unpublish] targets={report.targets} matched={report.matched} "
            f"removed={report.removed} absent={report.absent} failed={report.failed}",
            file=sys.stderr,
        )
        if report.dry_run_path is not None:
            print(
                f"[unpublish] dry-run: wrote {report.matched} match(es) to {report.dry_run_path}",
                file=sys.stderr,
            )
            return PublishOutcome(ok=True, exit_code=EXIT_CLEAN, message="dry-run")

        for result in report.results:
            print(f"[unpublish]   {result.finding_id}: {result.status}", file=sys.stderr)
        return PublishOutcome(ok=report.ok, exit_code=EXIT_CLEAN if report.ok else EXIT_ERROR)


def _publish_arguments(
    request: PublishRequest | str,
    options: object | None,
) -> tuple[str, PublishOptions]:
    if isinstance(request, PublishRequest):
        session_dir = str(request.session_dir)
        options = request.options
    else:
        session_dir = request
    assert isinstance(options, PublishOptions)
    return session_dir, options


AzureDevOpsSink = AzureDevOpsPublisher

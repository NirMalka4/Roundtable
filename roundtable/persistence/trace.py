"""persistence.trace: slim session artifacts + atomic writes.

Implements slim persistence artifacts:

  * a session directory (``<base>/<session_id>/``),
  * ``trace.json`` — the neutral structured run record: per-agent final output +
    validity + run metadata, plus an opaque caller-supplied
    ``overlay`` of domain fields (for the review config: verdict + finding counts +
    subject/provenance),
  * a human report (``<report_filename>``, default ``report.md``) written verbatim
    from the caller,
  * ``session.log`` — an append-only run log.

This module is **config-agnostic**: it owns none of the review-domain concepts
(verdict / findings / per-repo index). The caller assembles those (see
:mod:`roundtable.review.trace_overlay`) and hands them in as an opaque overlay +
report + exit code.

Two correctness-bearing invariants are honoured:

  * **atomic writes** (AUDIT-011): every file is written to a
    sibling ``*.tmp`` then ``os.replace``-d into place, so a crashed run never
    leaves a half-written ``trace.json``/report.
  * **output contract split**: a schema-less agent retains its first successful
    backend response unchanged; schema-backed output is valid only after accepted
    submission.

Per-attempt context/response/manifest ``.md`` dumps are intentionally NOT written
here (deferred behind a future ``--debug`` flag per the plan); only the final,
slim surface is persisted.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roundtable.backend import (
    EMPTY_USAGE,
    AgentUsage,
    ExecutionPolicy,
    anomalous_finish_reasons,
    as_float,
    format_ai_credits,
    merge_usage,
    retain_submission_diagnostics,
)

from ..result_access import to_response_map
from .trace_keys import TraceKey as K

if TYPE_CHECKING:
    from roundtable.graph import Configuration


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (tmp + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def create_session_dir(base_dir: Path, session_id: str) -> Path:
    """Create and return ``<base_dir>/<session_id>/``."""
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _agent_usage(outcome: Any) -> AgentUsage:
    """Extract the per-agent :class:`AgentUsage` from an outcome (or mapping)."""
    u = outcome.get("usage") if isinstance(outcome, Mapping) else getattr(outcome, "usage", None)
    return u if isinstance(u, AgentUsage) else EMPTY_USAGE


def _agent_tools_used(outcome: Any) -> list[str]:
    """Per-agent invoked tool names (P-03 telemetry), tolerant of mapping/outcome."""
    if isinstance(outcome, Mapping):
        tu = outcome.get("tools_used")
    else:
        tu = getattr(outcome, "tools_used", None)
    return [t for t in (tu or []) if isinstance(t, str)]


def _agent_wall_ms(outcome: Any) -> float:
    """Per-agent end-to-end subprocess wall-clock time in ms (0.0 when unmeasured)."""
    if isinstance(outcome, Mapping):
        v = outcome.get("wall_clock_ms_total", outcome.get("wallClockMs", 0.0))
    else:
        v = getattr(outcome, "wall_clock_ms_total", 0.0)
    return as_float(v)


def tool_stats(tools_used: list[str]) -> dict[str, int]:
    """Ordered ``{tool: count}`` over invoked tool names (first-seen order).

    Every tool the agent invoked is counted, so cross-tool usage/cost analysis
    needs no per-server allowlist. An MCP call is reported under its
    ``<server>-<tool>`` runtime name (the ``<server>/<tool>`` form is the
    *declaration* spelling in ``agent_graph.yaml``), so a consumer can split
    builtin from MCP by matching loaded-server prefixes.
    """
    counts: dict[str, int] = {}
    for name in tools_used:
        if isinstance(name, str) and name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _attempt_to_dict(ad: Any) -> dict[str, Any]:
    """Serialise one ``AttemptDetail`` or mapping for trace.json.

    ``errors`` and ``warnings`` are projected to candidate-safe diagnostic fields
    here, regardless of whether the caller supplied an object or mapping. The first
    failing gate and joined reason remain derivable from ``errors`` on read. Also
    persists ``backendOutcome`` whenever the backend reported a fault on the turn,
    plus redacted ``retryFeedback``, tool names, compact ``toolErrors``, and
    subprocess ``wallClockMs``.
    """
    if isinstance(ad, Mapping):
        attempt = int(ad.get("attempt", 0))
        model = ad.get("model", "")
        outcome = ad.get("outcome", "")
        retry_feedback = ad.get("retry_feedback") or ad.get("retryFeedback") or ""
        tools_invoked = ad.get("tools_invoked") or ad.get("toolsInvoked") or []
        tool_calls = ad.get("tool_calls") or ad.get("toolCalls") or []
        wall_clock_ms = as_float(ad.get("wall_clock_ms", ad.get("wallClockMs", 0.0)))
        usage = ad.get("usage")
        errors = ad.get("errors") or []
        diag_warnings = ad.get("warnings") or []
        execution_policy = ad.get("execution_policy") or ad.get("executionPolicy")
        backend_outcome = ad.get("backend_outcome") or ad.get("backendOutcome")
        submission_status = ad.get("submission_status") or ad.get("submissionStatus")
        submissions = ad.get("submissions") or []
    else:
        attempt = int(getattr(ad, "attempt", 0))
        model = getattr(ad, "model", "")
        outcome = getattr(ad, "outcome", "")
        retry_feedback = getattr(ad, "retry_feedback", "") or ""
        tools_invoked = getattr(ad, "tools_invoked", None) or []
        tool_calls = getattr(ad, "tool_calls", None) or []
        wall_clock_ms = as_float(getattr(ad, "wall_clock_ms", 0.0))
        usage = getattr(ad, "usage", None)
        errors = getattr(ad, "errors", None) or []
        diag_warnings = getattr(ad, "warnings", None) or []
        execution_policy = getattr(ad, "execution_policy", None)
        backend_outcome = getattr(ad, "backend_outcome", None)
        submission_status = getattr(ad, "submission_status", None)
        submissions = getattr(ad, "submissions", None) or []
    usage = usage if isinstance(usage, AgentUsage) else EMPTY_USAGE
    tools_invoked = [t for t in tools_invoked if isinstance(t, str)]
    d: dict[str, Any] = {K.ATTEMPT: attempt, K.MODEL: model, K.OUTCOME: outcome}
    if errors:
        d[K.ERRORS] = list(retain_submission_diagnostics(errors))
    if diag_warnings:
        d[K.WARNINGS] = list(retain_submission_diagnostics(diag_warnings))
    if retry_feedback:
        d[K.RETRY_FEEDBACK] = retry_feedback
    if tools_invoked:
        d[K.TOOLS_INVOKED] = tools_invoked
    tool_errors = [
        {
            key: value
            for key, value in (
                ("name", call.get("name")),
                ("toolError", call.get("toolError")),
                ("toolErrorRetention", call.get("toolErrorRetention")),
                ("durationMs", call.get("durationMs")),
                ("exitCode", call.get("exitCode")),
            )
            if value is not None
        }
        for call in tool_calls
        if isinstance(call, Mapping)
        and isinstance(call.get("toolError"), Mapping)
        and call.get("name")
    ]
    if tool_errors:
        d[K.TOOL_ERRORS] = tool_errors
    if wall_clock_ms:
        d[K.WALL_CLOCK_MS] = round(wall_clock_ms, 1)
    if not usage.is_empty:
        d[K.USAGE] = usage.to_dict()
    if isinstance(execution_policy, ExecutionPolicy):
        d[K.EXECUTION_POLICY] = execution_policy.to_dict()
    elif isinstance(execution_policy, Mapping):
        d[K.EXECUTION_POLICY] = dict(execution_policy)
    if submission_status:
        d[K.SUBMISSION_STATUS] = submission_status
    if backend_outcome:
        d[K.BACKEND_OUTCOME] = str(backend_outcome)
    if submissions:
        d[K.SUBMISSIONS] = [dict(item) for item in submissions if isinstance(item, Mapping)]
    return d


def _agent_attempts_detail(outcome: Any) -> list[Any]:
    """Per-backend-attempt records, tolerant of mapping/outcome."""
    if isinstance(outcome, Mapping):
        ad = outcome.get("attempts_detail")
    else:
        ad = getattr(outcome, "attempts_detail", None)
    return list(ad or [])


def _agent_entry(key: str, outcome: Any) -> dict[str, Any]:
    """Slim per-agent trace record. Accepts ``AgentRunOutcome`` or a mapping."""
    if isinstance(outcome, Mapping):
        response = outcome.get("response", "")
        valid = bool(outcome.get("valid", False))
        gate = outcome.get("gate")
        attempts = int(outcome.get("attempts", 0))
        submission_status = outcome.get("submission_status") or outcome.get("submissionStatus")
    else:
        response = getattr(outcome, "response", "")
        valid = bool(getattr(outcome, "valid", False))
        gate = getattr(outcome, "gate", None)
        attempts = int(getattr(outcome, "attempts", 0))
        submission_status = getattr(outcome, "submission_status", None)
    entry: dict[str, Any] = {
        K.AGENT: key,
        K.VALID: valid,
        K.GATE: gate,
        K.ATTEMPTS: attempts,
        K.RESPONSE: response if isinstance(response, str) else "",
    }
    if submission_status:
        entry[K.SUBMISSION_STATUS] = submission_status
    # Only emit a usage block when something was measured, so a no-usage run keeps
    # trace.json byte-stable (no empty usage keys).
    usage = _agent_usage(outcome)
    if not usage.is_empty:
        entry[K.USAGE] = usage.to_dict()
    # Per-agent tool-usage stats: every invoked tool → invocation count (first-seen
    # order). Omitted when the agent invoked no tools, keeping tool-free runs
    # byte-stable.
    stats = tool_stats(_agent_tools_used(outcome))
    if stats:
        entry[K.TOOL_STATS] = stats
    # Per-agent end-to-end wall-clock (model + tool + MCP time). Omitted when
    # unmeasured (0.0) to keep no-usage runs byte-stable.
    wall_ms = _agent_wall_ms(outcome)
    if wall_ms:
        entry[K.WALL_CLOCK_MS] = round(wall_ms, 1)
    # Per-attempt observability. Emitted only when the run was NOT a
    # single clean pass (a retry happened, the agent ended invalid, or an attempt was
    # non-valid), so the happy path keeps trace.json
    # byte-stable (no attemptsDetail key on first-try successes).
    detail = [_attempt_to_dict(a) for a in _agent_attempts_detail(outcome)]
    if detail and (
        attempts > 1
        or not valid
        or any(d[K.OUTCOME] not in {"valid", "raw_output", "submission_valid"} for d in detail)
        or any(d.get(K.WARNINGS) for d in detail)
        or any(d.get(K.TOOL_ERRORS) for d in detail)
        or any(d.get(K.EXECUTION_POLICY) for d in detail)
        or any(d.get(K.SUBMISSION_STATUS) for d in detail)
        or any(d.get(K.BACKEND_OUTCOME) for d in detail)
    ):
        entry[K.ATTEMPTS_DETAIL] = detail
    # Surface run-level notices such as session adoption and terminal abort as
    # structured entries. Per-attempt failures remain on the attempt itself.
    if isinstance(outcome, Mapping):
        warnings = outcome.get("warnings") or []
    else:
        warnings = getattr(outcome, "warnings", None) or []
    if warnings:
        entry[K.WARNINGS] = list(warnings)
    return entry


@dataclass
class PersistResult:
    """Where artifacts landed + the process exit code passed in by the caller."""

    session_dir: Path
    trace_path: Path
    report_path: Path
    log_path: Path
    exit_code: int


def build_trace(
    *,
    session_id: str,
    agent_outcomes: Mapping[str, Any],
    overlay: Mapping[str, Any] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    mcp_prewarm: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the slim ``trace.json`` dict (pure — no I/O, so it is unit-testable).

    Writes the **neutral** run record: session identity, per-agent outcomes, run
    timing, session cost, and the MCP pre-flight block.

    ``overlay`` — when supplied — is an **opaque** mapping of caller-owned fields
    (for the review domain: the verdict surface, finding ``counts``, and the
    ``subject``/``diffStat``/``provenance`` blocks assembled by
    :func:`roundtable.review.trace_overlay.build_overlay`) merged verbatim right
    after the session id. The persistence core neither produces nor interprets it,
    so a non-review config records its own domain fields the same way.

    ``mcp_prewarm`` — when supplied — records the pre-flight warm-up/health-probe
    outcome per server (``{name, verdict, pruned}``, plus a ``stderr`` tail on a
    non-``ready`` server): the ``verdict`` disambiguates a server pruned as
    unreachable from one that simply loaded no tools, ``pruned`` marks the ones
    dropped from the prompt+config this run, and ``stderr`` carries the last few
    child-stderr lines so a failed warm-up explains itself. Omitted / empty ⇒ key
    absent (byte-stable).
    """
    agents = [_agent_entry(k, agent_outcomes[k]) for k in agent_outcomes]
    session_usage = merge_usage(_agent_usage(agent_outcomes[k]) for k in agent_outcomes)
    trace: dict[str, Any] = {K.SESSION_ID: session_id}
    if overlay:
        trace.update(overlay)
    trace[K.AGENT_COUNT] = len(agents)
    trace[K.STARTED_AT] = started_at
    trace[K.FINISHED_AT] = finished_at or _utc_iso()
    trace[K.AGENTS] = agents
    # Session-level cost rollup; omitted when nothing was measured so the
    # no-usage trace.json stays byte-stable.
    if not session_usage.is_empty:
        trace[K.USAGE] = session_usage.to_dict()
    if mcp_prewarm:
        trace[K.MCP_PREWARM] = [dict(entry) for entry in mcp_prewarm]
    return trace


def render_cost_footer(usage: AgentUsage) -> str:
    """Markdown cost footer for ``verdict.md``. ``''`` when nothing measured."""
    if usage.is_empty:
        return ""
    lines = ["", "---", "## Cost"]
    if usage.input_tokens:
        lines.append(f"- Input tokens: {usage.input_tokens:,}")
        if usage.cache_read_tokens:
            lines.append(f"  - of which cached: {usage.cache_read_tokens:,}")
        lines.append(f"- Total tokens: {usage.total_tokens:,}")
    lines.append(f"- Output tokens: {usage.output_tokens:,}")
    if usage.reasoning_tokens:
        lines.append(f"  - of which reasoning: {usage.reasoning_tokens:,}")
    lines += [
        f"- LLM rounds: {usage.rounds}",
    ]
    if usage.billing.is_complete:
        assert usage.billing.total_nano_aiu is not None
        lines.append(f"- AI Credits consumed: {format_ai_credits(usage.billing.total_nano_aiu)}")
    anomalies = anomalous_finish_reasons(usage.finish_reasons)
    if anomalies:
        lines.append(f"- ⚠ Abnormal stop: {', '.join(anomalies)}")
    lines += [
        "",
        "> Token counts are advisory observability — derived from the run, "
        "never a parity/gate target.",
    ]
    return "\n".join(lines) + "\n"


def persist_session(
    base_dir: Path,
    *,
    session_id: str,
    agent_outcomes: Mapping[str, Any],
    overlay: Mapping[str, Any] | None = None,
    started_at: str | None = None,
    log_lines: list[str] | None = None,
    report_md: str | None = None,
    report_filename: str = "report.md",
    exit_code: int = 0,
    outcome_label: str | None = None,
    mcp_prewarm: list[Mapping[str, Any]] | None = None,
    git_context: Mapping[str, Any] | None = None,
    configuration: Configuration | None = None,
    source_payloads: Mapping[str, str] | None = None,
    replay_context: Mapping[str, Any] | None = None,
) -> PersistResult:
    """Persist ``trace.json`` + a human report (+ optional log) atomically.

    Writes the **neutral** session artifacts and merges the caller's opaque
    ``overlay`` into ``trace.json`` (see :func:`build_trace`). The persistence core
    owns none of the review-domain concepts (verdict / findings / index) — those are
    assembled by the caller and handed in.

    ``overlay`` — opaque caller-owned domain fields merged into ``trace.json``.

    ``report_md`` — when supplied — is written verbatim as ``<report_filename>``
    (the review shell passes the rich human report and ``report_filename="verdict.md"``).
    When omitted an empty report file is written so the artifact always exists.

    ``report_filename`` — on-disk name of the human report artifact (default
    ``report.md``).

    ``exit_code`` — the process exit code returned on :class:`PersistResult`
    (computed by the caller from its own outcome; the core does not derive it).

    ``outcome_label`` — an optional run-outcome label forwarded to the analytics
    ``usage-summary.json`` (the review shell passes the verdict string). Omitted ⇒
    the summary records ``None``.

    ``git_context`` — when supplied — is written as a standalone
    ``git-context.json`` recording how the review workspace was resolved (mode,
    snapshot/base SHA, add_dirs, cwd, discovery decision) so a run is auditable
    without re-deriving it from process state.

    Returns the artifact paths and the caller-supplied process exit code. The
    per-repo derived index is NOT rebuilt here (a domain concern the caller owns).
    """
    session_dir = create_session_dir(base_dir, session_id)
    finished_at = _utc_iso()

    trace = build_trace(
        session_id=session_id,
        agent_outcomes=agent_outcomes,
        overlay=overlay,
        started_at=started_at,
        finished_at=finished_at,
        mcp_prewarm=mcp_prewarm,
    )
    trace_path = session_dir / "trace.json"
    write_atomic(trace_path, json.dumps(trace, indent=2, ensure_ascii=False))

    # graph.json — a point-in-time snapshot of the agent graph's static metadata
    # (emoji / runtime / deps / delivery label / description). The offline report
    # reads its enrichment from THIS artifact, so it never imports engine config
    # and never mis-renders a historical run with a later-edited graph.
    from roundtable.reporting import snapshot_to_json

    from .graph_snapshot import build_graph_snapshot

    write_atomic(
        session_dir / "graph.json",
        snapshot_to_json(build_graph_snapshot(configuration)),
    )

    # Configuration identity is separate from the report-oriented graph snapshot:
    # artifact operations need to restore the bundle's executable semantics.
    from roundtable.graph import describe_active_configuration

    write_atomic(
        session_dir / "configuration.json",
        json.dumps(
            describe_active_configuration(configuration),
            indent=2,
            ensure_ascii=False,
        ),
    )

    # Analytics rollup — cost / performance / tool-usage view of the same
    # per-agent surface, for observability (not a gate). Always written; the
    # builder is pure and tolerates empty runs.
    from .usage_summary import build_usage_summary

    usage_summary = build_usage_summary(
        session_id=session_id,
        outcome_label=outcome_label,
        agent_outcomes=agent_outcomes,
        started_at=started_at,
        finished_at=finished_at,
        mcp_prewarm=mcp_prewarm,
    )
    write_atomic(
        session_dir / "usage-summary.json",
        json.dumps(usage_summary, indent=2, ensure_ascii=False),
    )

    # Re-emit the normalized raw_results.json so review, report-render and
    # publish all consume ONE shape ({agent: {"response": str}}); the TS port did
    # this and the py port dropped it, which is why the two shapes diverged.
    raw_results = to_response_map(agent_outcomes)
    write_atomic(
        session_dir / "raw_results.json", json.dumps(raw_results, indent=2, ensure_ascii=False)
    )

    # git-context.json — how the review workspace was resolved (mode, SHAs,
    # add_dirs, cwd, discovery decision). Standalone so observability tooling can
    # read it without parsing trace.json. Best-effort at the callers' discretion:
    # only written when the CLI supplies it (unit/legacy callers omit it).
    if git_context is not None:
        write_atomic(
            session_dir / "git-context.json",
            json.dumps(dict(git_context), indent=2, ensure_ascii=False),
        )

    if source_payloads is not None:
        write_atomic(
            session_dir / "source-payloads.json",
            json.dumps(dict(source_payloads), indent=2, ensure_ascii=False),
        )
    if replay_context is not None:
        write_atomic(
            session_dir / "replay-context.json",
            json.dumps(dict(replay_context), indent=2, ensure_ascii=False),
        )

    # agent-invocations.json — REMOVED. Its per-agent redacted tool calls now live
    # inside usage-summary.json (perAgent[].attemptsDetail[].toolCalls); the
    # workspace facts it echoed (addDirs / cwd) are authoritative in git-context.json.

    report_path = session_dir / report_filename
    rendered = report_md if report_md is not None else ""
    # Append the cost footer. Only touch the body when something was measured, so
    # a no-usage run keeps the report byte-stable.
    session_usage = merge_usage(_agent_usage(agent_outcomes[k]) for k in agent_outcomes)
    footer = render_cost_footer(session_usage)
    if footer:
        rendered = rendered.rstrip("\n") + "\n" + footer
    write_atomic(report_path, rendered)

    log_path = session_dir / "session.log"
    if log_lines:
        with log_path.open("a", encoding="utf-8") as fh:
            for line in log_lines:
                fh.write(line.rstrip("\n") + "\n")

    return PersistResult(
        session_dir=session_dir,
        trace_path=trace_path,
        report_path=report_path,
        log_path=log_path,
        exit_code=exit_code,
    )

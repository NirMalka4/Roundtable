"""persistence.usage_summary: derive the ``usage-summary.json`` analytics artifact.

A per-run observability rollup built from the same per-agent ``AgentRunOutcome``
surface ``trace.json`` consumes — but reshaped for *cost / performance / tool
analysis* rather than for the publish gate. It answers "what did this
run cost, where did the time go, which tools/MCP servers were exercised, what did
each agent actually read, and what went wrong" from the SDK ``assistant.usage``
stream. Token counts include the per-agent input/output/cache/reasoning breakdown
(the SDK surfaces input and cached tokens per API call — see ``runtime/usage.py``);
the breakdown keys are omitted when a run does not surface them.

Each ``perAgent[].attemptsDetail[]`` carries that attempt's redacted ``toolCalls``
(``{name, args?, ok?, toolError?}`` — allowlisted file/pattern/PR args plus safe
SDK-supplied failure provenance): the per-agent record of which files/patterns/PRs
each agent read and whether each call succeeded. Workspace facts
(``add_dirs`` / ``cwd``) are authoritative in ``git-context.json``.

``build_usage_summary`` is **pure** (no I/O) and tolerant of both
``AgentRunOutcome`` objects and plain mappings (mirroring ``trace.py``), so it is
directly unit-testable. All figures are advisory observability — deterministically
derived from the run, never a parity or gate target.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from roundtable.backend import (
    EMPTY_USAGE,
    NOT_APPLICABLE_BILLING,
    AgentUsage,
    BillingValue,
    ExecutionPolicy,
    anomalous_finish_reasons,
    as_float,
)

from .tool_redaction import redact_timeout_snapshot

_TOP_N = 5


def _get(outcome: Any, snake: str, camel: str | None = None, default: Any = None) -> Any:
    """Read ``snake`` (attr) or ``camel`` (mapping key) from an outcome-or-mapping."""
    if isinstance(outcome, Mapping):
        return outcome.get(snake, outcome.get(camel, default) if camel else default)
    return getattr(outcome, snake, default)


def _usage(outcome: Any) -> AgentUsage:
    u = _get(outcome, "usage")
    return u if isinstance(u, AgentUsage) else EMPTY_USAGE


def _token_metrics(u: AgentUsage) -> dict[str, int]:
    """Per-agent token counts for a usage-summary row.

    ``outputTokens`` is always present (mirrors the historical shape); the
    input/cache/reasoning/total breakdown is omitted when zero so a run that never
    surfaces it (e.g. the mock runner) keeps the artifact compact and stable.
    """
    metrics: dict[str, int] = {"outputTokens": u.output_tokens}
    if u.input_tokens:
        metrics["inputTokens"] = u.input_tokens
        metrics["totalTokens"] = u.total_tokens
    for key, val in (
        ("cacheReadTokens", u.cache_read_tokens),
        ("cacheWriteTokens", u.cache_write_tokens),
        ("reasoningTokens", u.reasoning_tokens),
    ):
        if val:
            metrics[key] = val
    return metrics


def _billing_metrics(u: AgentUsage) -> dict[str, dict[str, Any]]:
    return {"billing": u.billing.to_dict()}


def _tools_used(outcome: Any) -> list[str]:
    return [t for t in (_get(outcome, "tools_used", "toolsUsed") or []) if isinstance(t, str)]


def _prewarm_servers(mcp_prewarm: list[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Session MCP servers from the pre-flight probe: ``[{name, verdict}]``.

    ``mcpPrewarm`` (``runtime.mcp_prewarm``) is the only runtime MCP load/health
    signal the SDK path produces, so it is the SSOT for both the builtin-vs-MCP
    tool split (via server-name prefixes) and the per-server verdict shown here.
    """
    out: list[dict[str, Any]] = []
    for e in mcp_prewarm or []:
        name = e.get("name") if isinstance(e, Mapping) else None
        if isinstance(name, str) and name:
            out.append({"name": name, "verdict": e.get("verdict")})
    return out


def _attempts_detail(outcome: Any) -> list[Any]:
    return list(_get(outcome, "attempts_detail", "attemptsDetail") or [])


def _attempt_field(ad: Any, snake: str, camel: str, default: Any = None) -> Any:
    if isinstance(ad, Mapping):
        return ad.get(snake, ad.get(camel, default))
    return getattr(ad, snake, default)


def _first_error_gate(errors: list[Mapping[str, Any]]) -> str | None:
    """The failing gate of an attempt: the first ``errors[]`` entry carrying one."""
    for e in errors:
        gate = e.get("gate")
        if gate:
            return gate
    return None


def _joined_error_reason(errors: list[Mapping[str, Any]]) -> str:
    """The attempt's reject reason: the joined ``errors[]`` messages."""
    return "; ".join(str(e["message"]) for e in errors if e.get("message"))


def _mcp_prefixes(servers: list[dict[str, Any]]) -> tuple[str, ...]:
    """``<server>-`` prefixes used by the CLI to namespace that server's MCP tools."""
    return tuple(f"{s['name']}-" for s in servers if isinstance(s.get("name"), str))


def _is_mcp_tool(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name.startswith(p) for p in prefixes)


def _cost_rollup(items: list[tuple[str, Any]]) -> dict[str, Any]:
    total = EMPTY_USAGE
    wall = 0.0
    invalid = 0
    for _key, oc in items:
        total = total.merge(_usage(oc))
        wall += as_float(_get(oc, "wall_clock_ms_total", "wallClockMs", 0.0))
        if not bool(_get(oc, "valid", "valid", False)):
            invalid += 1
    return {
        "agentCount": len(items),
        **_billing_metrics(total),
        **_token_metrics(total),
        "rounds": total.rounds,
        "apiDurationMs": round(total.duration_ms, 1),
        "sessionDurationMs": round(total.session_duration_ms, 1),
        "wallClockMs": round(wall, 1),
        "invalidCount": invalid,
    }


def _agent_row(key: str, oc: Any) -> dict[str, Any]:
    usage = _usage(oc)
    tools = _tools_used(oc)
    model = _get(oc, "model", "model", "")
    observed = _get(oc, "observed_model", "observedModel", "")
    return {
        "agent": key,
        "valid": bool(_get(oc, "valid", "valid", False)),
        "gate": _get(oc, "gate", "gate"),
        "submissionStatus": _get(oc, "submission_status", "submissionStatus"),
        "attempts": int(_get(oc, "attempts", "attempts", 0) or 0),
        "model": model if isinstance(model, str) else "",
        "observedModel": observed if isinstance(observed, str) else "",
        **_billing_metrics(usage),
        **_token_metrics(usage),
        "rounds": usage.rounds,
        "apiDurationMs": round(usage.duration_ms, 1),
        "sessionDurationMs": round(usage.session_duration_ms, 1),
        "wallClockMs": round(as_float(_get(oc, "wall_clock_ms_total", "wallClockMs", 0.0)), 1),
        "toolCount": len(tools),
        "attemptsDetail": [_attempt_row(a) for a in _attempts_detail(oc)],
    }


def _attempt_row(ad: Any) -> dict[str, Any]:
    usage = _attempt_field(ad, "usage", "usage")
    usage = usage if isinstance(usage, AgentUsage) else EMPTY_USAGE
    tool_calls = [
        dict(c)
        for c in (_attempt_field(ad, "tool_calls", "toolCalls") or [])
        if isinstance(c, Mapping) and c.get("name")
    ]
    errors = [e for e in (_attempt_field(ad, "errors", "errors") or []) if isinstance(e, Mapping)]
    warnings = [
        warning
        for warning in (_attempt_field(ad, "warnings", "warnings") or [])
        if isinstance(warning, Mapping)
    ]
    events = [
        dict(event)
        for event in (_attempt_field(ad, "events", "events") or [])
        if isinstance(event, Mapping) and event.get("type")
    ]
    row = {
        "attempt": int(_attempt_field(ad, "attempt", "attempt", 0) or 0),
        "model": _attempt_field(ad, "model", "model", ""),
        "observedModel": _attempt_field(ad, "observed_model", "observedModel", ""),
        "outcome": _attempt_field(ad, "outcome", "outcome", ""),
        "submissionStatus": _attempt_field(ad, "submission_status", "submissionStatus"),
        "submissions": [
            dict(item)
            for item in (_attempt_field(ad, "submissions", "submissions") or [])
            if isinstance(item, Mapping)
        ],
        # Per-attempt gate/reason are not stored; the failing gate
        # is the first errors[] entry carrying one, the reason is the joined
        # error messages. Derived here so the analytics view stays self-contained.
        "gate": _first_error_gate(errors),
        "rejectReason": _joined_error_reason(errors),
        # Redacted {name, args?} calls this attempt made — which files/patterns it read.
        "toolCalls": tool_calls,
        "sdkEvents": events,
        "wallClockMs": round(as_float(_attempt_field(ad, "wall_clock_ms", "wallClockMs", 0.0)), 1),
        **_billing_metrics(usage),
        **_token_metrics(usage),
    }
    if warnings:
        row["warnings"] = [dict(warning) for warning in warnings]
    retry_feedback = _attempt_field(ad, "retry_feedback", "retryFeedback", "")
    if retry_feedback:
        row["retryFeedback"] = retry_feedback
    backend_outcome = _attempt_field(ad, "backend_outcome", "backendOutcome")
    if backend_outcome:
        row["backendOutcome"] = str(backend_outcome)
    tool_calls_truncated = bool(
        _attempt_field(ad, "tool_calls_truncated", "toolCallsTruncated", False)
    )
    if tool_calls_truncated:
        row["toolCallRetention"] = {
            "truncated": True,
            "omittedCount": int(
                _attempt_field(ad, "tool_calls_omitted_count", "toolCallsOmittedCount", 0)
            ),
        }
    events_truncated = bool(_attempt_field(ad, "events_truncated", "eventsTruncated", False))
    if events_truncated:
        row["sdkEventRetention"] = {
            "truncated": True,
            "omittedCount": int(
                _attempt_field(ad, "events_omitted_count", "eventsOmittedCount", 0)
            ),
        }
    timeout_phase = _attempt_field(ad, "timeout_phase", "timeoutPhase")
    if timeout_phase:
        row["timeoutPhase"] = timeout_phase
    timeout_snapshot = _attempt_field(ad, "timeout_snapshot", "timeoutSnapshot") or []
    if timeout_snapshot:
        row["timeoutSnapshot"] = redact_timeout_snapshot(timeout_snapshot)
    execution_policy = _attempt_field(ad, "execution_policy", "executionPolicy")
    if isinstance(execution_policy, ExecutionPolicy):
        row["executionPolicy"] = execution_policy.to_dict()
    elif isinstance(execution_policy, Mapping):
        row["executionPolicy"] = dict(execution_policy)
    return row


def _tool_usage(items: list[tuple[str, Any]], prefixes: tuple[str, ...]) -> dict[str, Any]:
    """Cross-agent ``{tool: {calls, agents}}`` split into builtin vs MCP."""
    builtin: dict[str, dict[str, int]] = {}
    mcp: dict[str, dict[str, int]] = {}
    for _key, oc in items:
        seen_here: set[str] = set()
        for name in _tools_used(oc):
            bucket = mcp if _is_mcp_tool(name, prefixes) else builtin
            rec = bucket.setdefault(name, {"calls": 0, "agents": 0})
            rec["calls"] += 1
            if name not in seen_here:
                rec["agents"] += 1
                seen_here.add(name)
    return {
        "builtin": builtin,
        "mcp": mcp,
        "totals": {
            "builtinCalls": sum(r["calls"] for r in builtin.values()),
            "mcpCalls": sum(r["calls"] for r in mcp.values()),
            "distinctTools": len(builtin) + len(mcp),
        },
    }


def _mcp_usage(
    items: list[tuple[str, Any]], servers: list[dict[str, Any]], prefixes: tuple[str, ...]
) -> dict[str, Any]:
    by_agent: dict[str, list[str]] = {}
    for key, oc in items:
        invoked: list[str] = []
        seen: set[str] = set()
        for name in _tools_used(oc):
            if _is_mcp_tool(name, prefixes) and name not in seen:
                invoked.append(name)
                seen.add(name)
        if invoked:
            by_agent[key] = invoked
    return {"servers": servers, "byAgent": by_agent}


def _performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_wall = sorted(rows, key=lambda r: r["wallClockMs"], reverse=True)[:_TOP_N]
    by_api = sorted(rows, key=lambda r: r["apiDurationMs"], reverse=True)[:_TOP_N]
    retries = sorted(
        (r for r in rows if r["attempts"] > 1), key=lambda r: r["attempts"], reverse=True
    )[:_TOP_N]
    return {
        "slowestByWallMs": [
            {"agent": r["agent"], "wallClockMs": r["wallClockMs"]} for r in by_wall
        ],
        "slowestByApiMs": [
            {"agent": r["agent"], "apiDurationMs": r["apiDurationMs"]} for r in by_api
        ],
        "longestRetries": [{"agent": r["agent"], "attempts": r["attempts"]} for r in retries],
    }


def _validation_tax(rows: list[dict[str, Any]]) -> dict[str, Any]:
    histogram: dict[str, int] = {}
    billing_on_rejects = NOT_APPLICABLE_BILLING
    for r in rows:
        histogram[str(r["attempts"])] = histogram.get(str(r["attempts"]), 0) + 1
        for ad in r["attemptsDetail"]:
            if ad["outcome"] not in {"valid", "raw_output", "submission_valid"}:
                billing_on_rejects = billing_on_rejects.merge(
                    BillingValue.from_dict(ad.get("billing"))
                )
    return {
        "attemptsHistogram": dict(sorted(histogram.items())),
        "billingOnRejects": billing_on_rejects.to_dict(),
        "retriedAgents": [r["agent"] for r in rows if r["attempts"] > 1],
    }


def _anomalies(items: list[tuple[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    timed_out = [
        key
        for key, oc in items
        if "timeout" in str(_get(oc, "last_error", "lastError", "") or "").lower()
    ]
    # Agents whose model stopped abnormally (truncation / content filter) on any
    # round — a likely cause of a missing or rejected schema submission.
    truncated_or_filtered = [
        {"agent": key, "reasons": list(reasons)}
        for key, oc in items
        if (reasons := anomalous_finish_reasons(_usage(oc).finish_reasons))
    ]
    # Agents the runtime served on a model other than the one the graph declared.
    # The runtime substitutes silently rather than refusing, so without this the
    # declared model would be the only record and could never be contradicted.
    rerouted = [
        {"agent": r["agent"], "declared": r["model"], "observed": r["observedModel"]}
        for r in rows
        if r["model"] and r["observedModel"] and r["observedModel"] != r["model"]
    ]
    billing_warnings = [
        {
            "agent": row["agent"],
            "attempt": attempt["attempt"],
            "source": billing["source"],
            "warningCode": billing["warningCode"],
        }
        for row in rows
        for attempt in row["attemptsDetail"]
        if isinstance((billing := attempt.get("billing")), Mapping)
        and billing.get("status") == "unavailable"
        and billing.get("warningCode")
    ]
    return {
        "invalidAgents": [r["agent"] for r in rows if not r["valid"]],
        "timedOutAgents": timed_out,
        "truncatedOrFilteredAgents": truncated_or_filtered,
        "modelReroutedAgents": rerouted,
        "billingWarnings": billing_warnings,
    }


def build_usage_summary(
    *,
    session_id: str,
    outcome_label: str | None = None,
    agent_outcomes: Mapping[str, Any],
    started_at: str | None = None,
    finished_at: str | None = None,
    mcp_prewarm: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the ``usage-summary.json`` dict (pure — no I/O).

    Grain is session + agent + attempt: ``costRollup`` is the session total,
    ``perAgent[]`` carries per-agent figures with a nested ``attemptsDetail[]``,
    and the tool/mcp/performance/validationTax/anomalies sections cross-cut those.
    Tolerant of ``AgentRunOutcome`` values or plain mappings.

    ``outcome_label`` is an opaque run-outcome label recorded under ``verdict``
    (the review shell passes its verdict string); ``None`` ⇒ recorded as ``null``.
    """
    items = list(agent_outcomes.items())
    # Session MCP servers come from the pre-flight probe (mcpPrewarm) — the only
    # SDK-era MCP signal — and their names give the prefixes that split builtin
    # vs MCP tools.
    servers = _prewarm_servers(mcp_prewarm)
    prefixes = _mcp_prefixes(servers)

    rows = [_agent_row(key, oc) for key, oc in items]
    return {
        "sessionId": session_id,
        "verdict": outcome_label,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "costRollup": _cost_rollup(items),
        "perAgent": rows,
        "toolUsage": _tool_usage(items, prefixes),
        "mcpUsage": _mcp_usage(items, servers, prefixes),
        "performance": _performance(rows),
        "validationTax": _validation_tax(rows),
        "anomalies": _anomalies(items, rows),
    }

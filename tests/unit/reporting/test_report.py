"""Tests for the session-report visualization (roundtable.reporting).

Uses a tiny synthetic session directory rather than the multi-MB live artifact,
so the tricky cases are exercised in isolation: envelope edge parsing anchored on
the [STATE] suffix (no false edges from headings inside delivered content), graph
drift tolerance, deterministic empty dumps, fan-in aggregator bundling, and safe
HTML embedding of hostile content.

The session carries its own engine-neutral ``graph.json`` snapshot (exactly as a
real run persists), so enrichment is read from that artifact — the reporting
package imports no engine configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtable.reporting import (
    build_report,
    compute_layout,
    load_graph_snapshot,
    load_report_model,
)
from roundtable.reporting.snapshot import from_payload, snapshot_to_json, to_payload


def _base_snapshot_agents() -> list[dict]:
    """The fixture graph: mirrors the shape a real run persists to ``graph.json``.

    Enrichment (runtime, deps, delivery label, description) is read from THIS —
    no engine config is imported by the report.
    """
    return [
        {"key": "DeterministicPreScan", "runtime": "deterministic"},
        {
            "key": "Profiler_CodeMap",
            "runtime": "llm",
            "emoji": "🗺️",
            "description": "Maps the code.",
        },
        {
            "key": "Security",
            "runtime": "llm",
            "emoji": "🔒",
            "requiredDeps": ["Profiler_CodeMap"],
            "description": "Finds vulnerabilities.",
        },
        # Producer of the ``security_intent_pack`` delivery label (need not run).
        {
            "key": "SecurityIntentProfiler",
            "runtime": "llm",
            "deliveryLabel": "## security_intent_pack",
        },
    ]


def _write_graph_snapshot(root: Path, extra_agents: list[dict] | None = None) -> None:
    """Persist a ``graph.json`` snapshot for the fixture agents (+ any extras)."""
    payload = {
        "version": 1,
        "displayName": "roundtable",
        "agents": _base_snapshot_agents() + list(extra_agents or []),
    }
    (root / "graph.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_session(
    root: Path, *, dump: bool = True, extra_snapshot_agents: list[dict] | None = None
) -> Path:
    trace = {
        "sessionId": "sess-1",
        "verdict": "APPROVE_WITH_SUGGESTIONS",
        "verdictIcon": "⚠️",
        "counts": {"blocking": 0, "nonBlocking": 2, "all": 2, "security": 1},
        "agentCount": 3,
        "startedAt": "2026-01-01T00:00:00Z",
        "finishedAt": "2026-01-01T00:10:00Z",
        "usage": {
            "outputTokens": 100,
            "rounds": 5,
            "billing": {
                "source": "copilot-sdk/session.usage.getMetrics",
                "status": "complete",
                "totalNanoAiu": 2_500_000_000.0,
                "totalPremiumRequestCost": 3.75,
            },
        },
        "agents": [
            {"agent": "DeterministicPreScan", "valid": True, "gate": None, "attempts": 1},
            {
                "agent": "Profiler_CodeMap",
                "valid": True,
                "gate": "all",
                "attempts": 1,
                "usage": {
                    "outputTokens": 60,
                    "rounds": 3,
                    "billing": {
                        "source": "copilot-sdk/session.usage.getMetrics",
                        "status": "complete",
                        "totalNanoAiu": 1_000_000_000.0,
                        "totalPremiumRequestCost": 1.25,
                    },
                },
                "wallClockMs": 1000.0,
            },
            {
                "agent": "Security",
                "valid": True,
                "gate": "all",
                "attempts": 2,
                "usage": {
                    "outputTokens": 40,
                    "rounds": 2,
                    "billing": {
                        "source": "copilot-sdk/session.usage.getMetrics",
                        "status": "complete",
                        "totalNanoAiu": 1_500_000_000.0,
                        "totalPremiumRequestCost": 2.5,
                    },
                },
                "wallClockMs": 2000.0,
            },
        ],
    }
    usage = {
        "costRollup": {
            "billing": {
                "source": "copilot-sdk/session.usage.getMetrics",
                "status": "complete",
                "totalNanoAiu": 2_500_000_000.0,
                "totalPremiumRequestCost": 3.75,
            },
            "outputTokens": 100,
            "inputTokens": 900,
            "totalTokens": 1000,
            "cacheReadTokens": 300,
            "cacheWriteTokens": 250,
            "reasoningTokens": 40,
            "rounds": 5,
            "apiDurationMs": 3000.0,
            "wallClockMs": 3000.0,
            "invalidCount": 0,
        },
        "perAgent": [
            {
                "agent": "Security",
                "valid": True,
                "attempts": 2,
                "models": ["gpt-5.4", "gpt-5.4"],
                "billing": {
                    "source": "copilot-sdk/session.usage.getMetrics",
                    "status": "complete",
                    "totalNanoAiu": 1_500_000_000.0,
                    "totalPremiumRequestCost": 2.5,
                },
                "outputTokens": 100,
                "inputTokens": 900,
                "totalTokens": 1000,
                "cacheReadTokens": 300,
                "cacheWriteTokens": 250,
                "reasoningTokens": 40,
                "attemptsDetail": [
                    {
                        "attempt": 1,
                        "model": "gpt-5.4",
                        "outcome": "ovg_reject",
                        "gate": "format",
                        "rejectReason": "Invalid JSON: Unexpected token 'N'",
                        "billing": {
                            "source": "copilot-sdk/session.usage.getMetrics",
                            "status": "complete",
                            "totalNanoAiu": 500_000_000.0,
                            "totalPremiumRequestCost": 0.75,
                        },
                        "toolCalls": [
                            {"name": "powershell"},
                            {"name": "powershell", "args": {"path": "src/x.py"}},
                        ],
                        "wallClockMs": 1500.0,
                    },
                    {
                        "attempt": 2,
                        "model": "gpt-5.4",
                        "outcome": "valid",
                        "gate": None,
                        "billing": {
                            "source": "copilot-sdk/session.usage.getMetrics",
                            "status": "complete",
                            "totalNanoAiu": 1_000_000_000.0,
                            "totalPremiumRequestCost": 1.75,
                        },
                    },
                ],
            },
        ],
        "performance": {"slowestByWallMs": [{"agent": "Security", "wallClockMs": 2000.0}]},
        "validationTax": {
            "retriedAgents": ["Security"],
            "billingOnRejects": {
                "source": "copilot-sdk/session.usage.getMetrics",
                "status": "complete",
                "totalNanoAiu": 500_000_000.0,
                "totalPremiumRequestCost": 0.75,
            },
        },
        "anomalies": {},
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (root / "usage-summary.json").write_text(json.dumps(usage), encoding="utf-8")
    _write_graph_snapshot(root, extra_snapshot_agents)

    if dump:
        agents = root / "agents"
        # Security's context.md: two real envelope edges (a [REQUIRED] header and
        # an [OPTIONAL] delivery label), plus a heading WITHOUT a state suffix
        # embedded in delivered content — the latter must NOT become an edge.
        sec = agents / "Security"
        sec.mkdir(parents=True)
        (sec / "system.md").write_text("You are Security.", encoding="utf-8")
        (sec / "context.md").write_text(
            "## Change Under Review\nblah\n\n"
            "## Context from Profiler_CodeMap [REQUIRED]\n"
            "the delivered payload itself contains a heading:\n"
            "## Context from GhostFromContent\n"  # no [STATE] suffix → ignored
            "\n## security_intent_pack [OPTIONAL]\n",
            encoding="utf-8",
        )
        (sec / "response.md").write_text('{"findings": []}', encoding="utf-8")
        (sec / "manifest.json").write_text(
            json.dumps({"model": "gpt-5.4"}),
            encoding="utf-8",
        )
        # Deterministic node: empty system/context, only a response.
        det = agents / "DeterministicPreScan"
        det.mkdir(parents=True)
        (det / "system.md").write_text("", encoding="utf-8")
        (det / "context.md").write_text("", encoding="utf-8")
        (det / "response.md").write_text("No issues.", encoding="utf-8")
        (det / "manifest.json").write_text(json.dumps({}), encoding="utf-8")
    return root


def test_load_basic(tmp_path: Path):
    model = load_report_model(_write_session(tmp_path / "s"))
    keys = {n.key for n in model.nodes}
    assert {"DeterministicPreScan", "Profiler_CodeMap", "Security"} <= keys
    det = next(n for n in model.nodes if n.key == "DeterministicPreScan")
    assert det.runtime == "deterministic"
    sec = next(n for n in model.nodes if n.key == "Security")
    assert sec.runtime == "llm"
    assert sec.attempts == 2


def test_complete_sdk_billing_renders_only_ai_credits(tmp_path: Path):
    root = _write_session(tmp_path / "s")
    doc = build_report(root)
    assert "AI Credits consumed</td><td>2.5" in doc
    assert "AI Credits spent on rejects: 0.5" in doc
    assert "Premium requests" not in doc
    assert "premium spent" not in doc
    assert "totalPremiumRequestCost" not in doc


def test_incomplete_sdk_billing_omits_human_cost(tmp_path: Path):
    root = _write_session(tmp_path / "s")
    for filename in ("trace.json", "usage-summary.json"):
        path = root / filename
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if filename == "trace.json":
            artifact["usage"]["billing"] = {
                "source": "copilot-sdk/session.usage.getMetrics",
                "status": "unavailable",
                "warningCode": "sdk_usage_metrics_unavailable",
            }
            for agent in artifact["agents"]:
                if "usage" in agent:
                    agent["usage"]["billing"] = artifact["usage"]["billing"]
        else:
            artifact["costRollup"]["billing"] = {
                "source": "copilot-sdk/session.usage.getMetrics",
                "status": "unavailable",
                "warningCode": "sdk_usage_metrics_unavailable",
            }
            artifact["perAgent"][0]["billing"] = artifact["costRollup"]["billing"]
            artifact["validationTax"]["billingOnRejects"] = artifact["costRollup"]["billing"]
        path.write_text(json.dumps(artifact), encoding="utf-8")
    doc = build_report(root)
    assert "AI Credits consumed</td>" not in doc
    assert "AI Credits spent on rejects" not in doc
    assert '"ai_credits"' not in doc
    assert "Premium requests" not in doc


def test_legacy_premium_artifacts_load_without_relabeling_as_ai_credits(tmp_path: Path):
    root = _write_session(tmp_path / "legacy")
    trace_path = root / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["usage"].pop("billing")
    trace["usage"]["premiumRequests"] = 7.5
    for agent in trace["agents"]:
        usage = agent.get("usage")
        if usage:
            usage.pop("billing")
            usage["premiumRequests"] = 3.25
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    summary_path = root / "usage-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["costRollup"].pop("billing")
    summary["costRollup"]["premiumRequests"] = 7.5
    summary["perAgent"][0].pop("billing")
    summary["perAgent"][0]["premiumRequests"] = 3.25
    summary["validationTax"] = {
        "retriedAgents": ["Security"],
        "premiumRequestsOnRejects": 1.0,
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    model = load_report_model(root)
    assert model.meta.legacy_premium_requests == 7.5
    doc = build_report(root)
    assert "AI Credits consumed</td>" not in doc
    assert "AI Credits spent on rejects" not in doc
    assert '"ai_credits"' not in doc


def test_edge_parse_anchors_on_state_suffix(tmp_path: Path):
    """Headers without a [STATE] suffix are never edges; envelope headers are."""
    model = load_report_model(_write_session(tmp_path / "s"))
    into_security = {(e.src, e.provenance) for e in model.edges if e.dst == "Security"}
    assert ("Profiler_CodeMap", "observed-header") in into_security
    # The suffix-less heading inside delivered content is not an edge.
    assert not any(e.src == "GhostFromContent" for e in model.edges)
    # security_intent_pack maps to SecurityIntentProfiler via its delivery label.
    assert any(e.dst == "Security" and e.provenance == "observed-label" for e in model.edges)


def test_missing_trace_raises(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        load_report_model(tmp_path / "empty")


def test_no_dump_falls_back_to_static_edges(tmp_path: Path):
    """Without per-agent dumps, edges come from graph deps, tagged inferred."""
    model = load_report_model(_write_session(tmp_path / "s", dump=False))
    assert model.edges  # Security hard-deps Profiler_CodeMap in the graph
    assert all(e.provenance == "inferred-static" for e in model.edges)


def test_unknown_agent_tolerated(tmp_path: Path):
    root = _write_session(tmp_path / "s")
    trace = json.loads((root / "trace.json").read_text(encoding="utf-8"))
    trace["agents"].append({"agent": "GhostAgent", "valid": True, "attempts": 1})
    (root / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    model = load_report_model(root)
    ghost = next(n for n in model.nodes if n.key == "GhostAgent")
    assert ghost.known_in_graph is False
    assert any("GhostAgent" in w for w in model.warnings)


def _fanin_layout(tmp_path: Path, *, optional_count: int, required_count: int):
    optional = [f"OptionalProducer{i}" for i in range(optional_count)]
    required = [f"RequiredProducer{i}" for i in range(required_count)]
    producers = optional + required
    root = _write_session(
        tmp_path / "s",
        extra_snapshot_agents=[
            {"key": "Aggregator", "runtime": "deterministic"},
            *({"key": p, "runtime": "llm"} for p in producers),
        ],
    )
    trace = json.loads((root / "trace.json").read_text(encoding="utf-8"))
    for p in producers:
        trace["agents"].append(
            {"agent": p, "valid": True, "attempts": 1, "usage": {"outputTokens": 1}}
        )
    trace["agents"].append({"agent": "Aggregator", "valid": True, "attempts": 1})
    (root / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    agg = root / "agents" / "Aggregator"
    agg.mkdir(parents=True)
    (agg / "context.md").write_text(
        "".join(f"## Context from {p} [OPTIONAL]\n" for p in optional)
        + "".join(f"## Context from {p} [REQUIRED]\n" for p in required),
        encoding="utf-8",
    )
    (agg / "response.md").write_text("aggregated", encoding="utf-8")
    return compute_layout(load_report_model(root))


def test_high_optional_fanin_bundles_only_optional_edges(tmp_path: Path):
    """Required inputs stay explicit beside a high optional fan-in bundle."""
    layout = _fanin_layout(tmp_path, optional_count=6, required_count=1)
    bundles = {b.dst: b for b in layout.bundles}
    assert set(bundles["Aggregator"].sources) == {f"OptionalProducer{i}" for i in range(6)}
    required_edge = next(
        edge
        for edge in layout.edges
        if edge.dst == "Aggregator" and edge.src == "RequiredProducer0"
    )
    assert required_edge.required
    assert layout.positions[required_edge.src].layer < layout.positions[required_edge.dst].layer
    assert not any(
        edge.dst == "Aggregator" and edge.src.startswith("OptionalProducer")
        for edge in layout.edges
    )


def test_required_fanin_does_not_make_optional_edges_bundle_eligible(tmp_path: Path):
    """Eligibility depends on optional fan-in, not total inbound degree."""
    layout = _fanin_layout(tmp_path, optional_count=3, required_count=5)
    assert "Aggregator" not in {bundle.dst for bundle in layout.bundles}
    assert len([edge for edge in layout.edges if edge.dst == "Aggregator"]) == 8


def test_low_fanin_llm_node_is_not_bundled(tmp_path: Path):
    """An LLM node is never collapsed even with high fan-in, and a deterministic
    node below the threshold stays drawn — the heuristic gates on both."""
    model = load_report_model(_write_session(tmp_path / "s"))
    layout = compute_layout(model)
    # Security (llm, 2 inbound) is drawn, not bundled.
    assert "Security" not in {b.dst for b in layout.bundles}
    assert any(e.dst == "Security" for e in layout.edges)


def test_attempts_detail_and_submission_rendering(tmp_path: Path):
    """Per-attempt submission diagnostics load onto the node and render in the report."""
    root = _write_session(tmp_path / "s")
    model = load_report_model(root)
    sec = next(n for n in model.nodes if n.key == "Security")
    assert len(sec.attempts_detail) == 2
    assert sec.attempts_detail[0]["outcome"] == "ovg_reject"
    assert sec.attempts_detail[0]["gate"] == "format"

    doc = build_report(root)
    # Submission story + the reject reason are surfaced; the verbose tool list is not.
    assert "Submission attempts" in doc
    assert "Invalid JSON: Unexpected token" in doc
    assert '"toolCalls"' not in doc
    assert '"toolsInvoked"' not in doc
    # Retries panel (renamed from "Model failover") and metric tooltips present.
    assert "Retries" in doc
    assert "Elapsed (wall clock)" in doc
    assert "agent concurrency" in doc


def test_submission_status_and_actionable_correction_render_in_agent_panel(tmp_path: Path):
    root = _write_session(tmp_path / "submission")
    usage_path = root / "usage-summary.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    security = usage["perAgent"][0]
    security["submissionStatus"] = "rejected"
    security["attemptsDetail"][0].update(
        {
            "outcome": "submission_rejected",
            "submissionStatus": "rejected",
            "rejectReason": "[json_schema] claims: missing required field 'claims'",
            "submissions": [
                {
                    "status": "rejected",
                    "correction": 1,
                    "diagnosticFingerprint": "abc",
                    "terminalCompletion": False,
                }
            ],
        }
    )
    trace_path = root / "trace.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace_security = next(row for row in trace["agents"] if row["agent"] == "Security")
    trace_security["attemptsDetail"] = [
        {
            "attempt": security["attemptsDetail"][0]["attempt"],
            "retryFeedback": "OUTPUT NOT SUBMITTED — call roundtable_submit_output.",
        }
    ]
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    usage_path.write_text(json.dumps(usage), encoding="utf-8")
    doc = build_report(root)
    assert '"submission_status": "rejected"' in doc
    assert "missing required field 'claims'" in doc
    assert "Feedback provided for this retry:" in doc
    assert "OUTPUT NOT SUBMITTED" in doc


def test_runtime_signals_render_without_sensitive_tool_details(tmp_path: Path):
    root = _write_session(tmp_path / "s")
    path = root / "usage-summary.json"
    usage = json.loads(path.read_text(encoding="utf-8"))
    security = usage["perAgent"][0]
    security["wallClockMs"] = 2000.0
    security["apiDurationMs"] = 500.0
    attempt = security["attemptsDetail"][0]
    attempt.update(
        {
            "executionPolicy": {
                "shellInvocationCapSeconds": 120,
                "backgroundPollCapSeconds": 30,
                "detachedAllowed": False,
            },
            "timeoutPhase": "tool_running",
            "timeoutSnapshot": [
                {
                    "name": "powershell",
                    "state": "running",
                    "shellId": "secret-shell-id",
                    "activeAtTimeout": True,
                }
            ],
            "toolCalls": [
                {
                    "name": "powershell",
                    "args": {"command": "pytest --token report-secret"},
                    "ok": False,
                    "state": "failed",
                    "durationMs": 120000,
                    "exitCode": 1,
                    "toolError": {
                        "source": "sdk-host",
                        "type": "ToolTimeout",
                        "code": "TOOL_TIMEOUT",
                        "timeoutOwner": "builtin-tool",
                        "message": "deadline exceeded",
                    },
                },
                {"name": "stale-running", "ok": True, "state": "running"},
                {"name": "view", "ok": True, "state": "exited"},
            ],
            "toolCallRetention": {"truncated": True, "omittedCount": 3},
            "sdkEventRetention": {"truncated": True, "omittedCount": 5},
        }
    )
    usage["anomalies"] = {
        "timedOutAgents": ["Security"],
        "truncatedOrFilteredAgents": [{"agent": "Security", "reasons": ["length"]}],
        "modelReroutedAgents": [
            {"agent": "Security", "declared": "gpt-5.6-sol", "observed": "claude-sonnet-5"}
        ],
    }
    path.write_text(json.dumps(usage), encoding="utf-8")

    doc = build_report(root)
    assert "Runtime anomalies" in doc
    assert "Abnormal finish" in doc
    assert "gpt-5.6-sol → claude-sonnet-5" in doc
    assert "Slowest (non-API)" in doc
    assert "Non-API time" in doc
    assert "Backend execution policy" in doc
    assert "shell invocation ≤ " in doc
    assert '"shellInvocationCapSeconds": 120' in doc
    assert "Attempt diagnostics" in doc
    assert "timeout: " in doc
    assert '"timeoutPhase": "tool_running"' in doc
    assert "Active at timeout:" in doc
    assert "Tool failures:" in doc
    assert "source unavailable" in doc
    assert "Telemetry truncated:" in doc
    assert '"toolCallRetention": {"truncated": true, "omittedCount": 3}' in doc
    assert '"sdkEventRetention": {"truncated": true, "omittedCount": 5}' in doc
    assert "report-secret" not in doc
    assert "secret-shell-id" not in doc
    island = doc.split('<script type="application/json" id="report-data">', 1)[1].split(
        "</script>", 1
    )[0]
    signals = json.loads(island)["Security"]["attempts_detail"][0]["failedToolCalls"]
    assert [call["name"] for call in signals] == ["powershell"]
    assert signals[0]["toolError"] == {
        "source": "sdk-host",
        "type": "ToolTimeout",
        "code": "TOOL_TIMEOUT",
        "timeoutOwner": "builtin-tool",
        "message": "deadline exceeded",
    }


def _set_models(root: Path, agent: str, *, declared: str, observed: str) -> None:
    """Stamp declared/served model onto one agent's usage row and its attempts."""
    path = root / "usage-summary.json"
    usage = json.loads(path.read_text(encoding="utf-8"))
    for row in usage["perAgent"]:
        if row["agent"] != agent:
            continue
        row["model"], row["observedModel"] = declared, observed
        for att in row.get("attemptsDetail") or []:
            att["model"], att["observedModel"] = declared, observed
    path.write_text(json.dumps(usage), encoding="utf-8")


def test_a_served_model_matching_the_declaration_is_shown_once(tmp_path: Path):
    root = _write_session(tmp_path / "s")
    _set_models(root, "Security", declared="gpt-5.4", observed="gpt-5.4")
    model = load_report_model(root)
    sec = next(n for n in model.nodes if n.key == "Security")
    assert sec.observed_model == "gpt-5.4"
    assert not [w for w in model.warnings if "runtime served" in w]
    assert "declared model" not in build_report(root)


def test_a_rerouted_model_is_visible_without_opening_the_panel(tmp_path: Path):
    """The runtime once ignored the declared model while every artifact echoed the
    declaration — a divergence only counts if a human sees it. The panel needs a
    click, so the divergence is also raised as a run-level warning."""
    root = _write_session(tmp_path / "s")
    _set_models(root, "Security", declared="gpt-5.6-sol", observed="claude-sonnet-5")
    model = load_report_model(root)
    sec = next(n for n in model.nodes if n.key == "Security")
    assert sec.final_model == "gpt-5.6-sol"
    assert sec.observed_model == "claude-sonnet-5"
    assert any("gpt-5.6-sol" in w and "claude-sonnet-5" in w for w in model.warnings)

    doc = build_report(root)
    assert "declared model &#x27;gpt-5.6-sol&#x27;" in doc
    assert "the runtime served &#x27;claude-sonnet-5&#x27;" in doc
    assert '"observed_model": "claude-sonnet-5"' in doc  # reaches the panel too


def test_tool_usage_aggregation_and_rollup(tmp_path: Path):
    """Per-tool histogram is derived from attemptsDetail.toolCalls[].name (trace
    toolStats being absent), and the overview shows a session-wide rollup."""
    model = load_report_model(_write_session(tmp_path / "s"))
    sec = {n.key: n for n in model.nodes}["Security"]
    assert sec.tool_stats.get("powershell") == 2  # aggregated from two attempts' lists
    assert sec.tool_count == 2  # total derived when perAgent.toolCount is absent

    doc = build_report(_write_session(tmp_path / "s2"))
    assert "Tool usage (2 calls)" in doc
    assert "powershell: 2" in doc


def _with_tool_usage(root: Path, tool_usage: dict, mcp_usage: dict) -> Path:
    """Inject a ``toolUsage`` + ``mcpUsage`` section into a session's summary."""
    summary = json.loads((root / "usage-summary.json").read_text(encoding="utf-8"))
    summary["toolUsage"] = tool_usage
    summary["mcpUsage"] = mcp_usage
    (root / "usage-summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return root


def test_tool_usage_splits_builtin_vs_mcp_and_flags_unused_servers(tmp_path: Path):
    """When usage-summary carries the builtin/MCP split, the tile renders both
    buckets and lists every loaded MCP server, flagging ones no agent invoked."""
    root = _with_tool_usage(
        _write_session(tmp_path / "s"),
        tool_usage={
            "builtin": {"view": {"calls": 5, "agents": 2}},
            "mcp": {"ado-work-items-list": {"calls": 3, "agents": 1}},
            "totals": {"builtinCalls": 5, "mcpCalls": 3, "distinctTools": 2},
        },
        mcp_usage={
            "servers": [
                {"name": "ado-work-items", "verdict": "ready"},
                {"name": "ado-publish", "verdict": "ready"},
            ],
            "byAgent": {"Security": ["ado-work-items-list"]},
        },
    )
    model = load_report_model(root)
    assert model.meta.tool_usage["totals"]["mcpCalls"] == 3
    assert len(model.meta.mcp_usage["servers"]) == 2

    doc = build_report(root)
    assert "Tool usage (8 calls)" in doc  # 5 builtin + 3 MCP
    assert "Builtin (5):" in doc
    assert "MCP (3):" in doc
    assert "ado-work-items-list: 3" in doc
    assert "MCP servers" in doc
    # ado-publish loaded but no agent invoked it → flagged; ado-work-items used.
    assert "ado-publish" in doc
    assert "not invoked" in doc


def test_tool_usage_mcp_zero_calls_shows_none_invoked(tmp_path: Path):
    """Servers that all load but are never called render 'none invoked' for the
    MCP bucket and flag every server as not invoked (the live-PR case)."""
    root = _with_tool_usage(
        _write_session(tmp_path / "s"),
        tool_usage={
            "builtin": {"view": {"calls": 4, "agents": 1}},
            "mcp": {},
            "totals": {"builtinCalls": 4, "mcpCalls": 0, "distinctTools": 1},
        },
        mcp_usage={
            "servers": [{"name": "ado-publish", "verdict": "ready"}],
            "byAgent": {},
        },
    )
    doc = build_report(root)
    assert "MCP (0):" in doc
    assert "none invoked" in doc
    assert "not invoked" in doc  # the loaded-but-unused server flag


def test_cache_write_and_reasoning_tokens_captured_and_rendered(tmp_path: Path):
    """cacheWriteTokens + reasoningTokens flow from usage-summary into both the
    session rollup and per-agent node, and reach the rendered report."""
    model = load_report_model(_write_session(tmp_path / "s"))
    assert model.meta.cache_write_tokens == 250
    assert model.meta.reasoning_tokens == 40
    sec = {n.key: n for n in model.nodes}["Security"]
    assert sec.cache_write_tokens == 250
    assert sec.reasoning_tokens == 40

    doc = build_report(_write_session(tmp_path / "s2"))
    assert "Cache write" in doc  # session KV + per-agent panel label
    assert "Reasoning" in doc


def test_agent_role_description_and_graph_flow(tmp_path: Path):
    """Every node exposes a purpose blurb + its observed graph neighbourhood, and
    those fields reach the rendered panel payload."""
    model = load_report_model(_write_session(tmp_path / "s"))
    by = {n.key: n for n in model.nodes}
    sec = by["Security"]
    # Authored .agent.md description (or the system.md intro fallback) — never empty.
    assert sec.description
    # Graph neighbourhood is derived from the observed edges.
    assert "Profiler_CodeMap" in sec.consumes
    assert "Security" in by["Profiler_CodeMap"].feeds
    # A deterministic node with an empty system.md still gets a generic blurb.
    assert by["DeterministicPreScan"].description

    doc = build_report(_write_session(tmp_path / "s2"))
    assert '"description"' in doc and '"consumes"' in doc and '"feeds"' in doc


def test_subject_block_renders_links(tmp_path: Path):
    """A trace with a subject/diffStat/provenance block surfaces repo/PR/branch +
    a clickable PR link and a local file:// artifacts link."""
    root = _write_session(tmp_path / "s")
    trace = json.loads((root / "trace.json").read_text(encoding="utf-8"))
    trace["subject"] = {
        "mode": "pr",
        "repo": "ExampleRepo",
        "remoteUrl": "https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo",
        "prId": "123",
        "prTitle": "Consolidate entity evidence types",
        "sourceBranch": "user/x/feature",
        "targetBranch": "main",
        "baseSha": "abcdef1234567890",
        "sourceSha": "0987654321fedcba",
    }
    trace["diffStat"] = {
        "filesChanged": 3,
        "insertions": 120,
        "deletions": 14,
        "changedFiles": ["a.py", "b.py"],
    }
    trace["provenance"] = {
        "toolName": "roundtable",
        "toolVersion": "1.2.3",
        "graphConfigSha": "deadbeefcafe",
    }
    (root / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    model = load_report_model(root)
    assert model.meta.subject["prId"] == "123"
    assert model.meta.session_uri and model.meta.session_uri.startswith("file://")

    doc = build_report(root)
    assert ">Subject<" in doc
    # Clickable PR link (ADO web form derived from the remote).
    assert (
        'href="https://contoso.visualstudio.com/ExampleProject/_git/ExampleRepo/pullrequest/123"'
        in doc
    )
    assert "Consolidate entity evidence types" in doc
    assert "user/x/feature" in doc and "&rarr; main" in doc
    # Diff fingerprint + local artifacts link (Session row).
    assert "+120" in doc and "14" in doc
    assert 'href="file://' in doc
    assert "roundtable 1.2.3" in doc


def test_legacy_trace_shows_no_subject_card_but_session_row_is_clickable(tmp_path: Path):
    """A legacy trace (no subject) allocates no Subject tile, yet the run
    card's Session row is still a clickable local file:// link, and it renders
    offline (no http/https)."""
    doc = build_report(_write_session(tmp_path / "s"))
    assert ">Subject<" not in doc  # no near-empty subject tile without a subject block
    assert "pullrequest/" not in doc  # no PR link without a subject
    assert 'href="file://' in doc  # artifacts link lives on the Session row
    assert "https://" not in doc.replace("http://www.w3.org/2000/svg", "")


def test_html_escapes_hostile_content(tmp_path: Path):
    """A </script> in agent output must not break out of the JSON data island."""
    root = _write_session(tmp_path / "s")
    (root / "agents" / "Security" / "response.md").write_text(
        "</script><script>window.__pwned=1</script>", encoding="utf-8"
    )
    doc = build_report(root)
    assert "window.__pwned" in doc  # present as data …
    assert "</script><script>window.__pwned" not in doc  # … but not as a live tag
    assert "\\u003c/script>" in doc or "\\u003cscript>" in doc


def test_html_is_self_contained(tmp_path: Path):
    doc = build_report(_write_session(tmp_path / "s"))
    assert doc.startswith("<!DOCTYPE html>")
    assert "http://" not in doc.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in doc


def test_graph_snapshot_round_trips(tmp_path: Path):
    """The snapshot payload survives serialize→parse with every field intact."""
    root = _write_session(tmp_path / "s")
    snap = load_graph_snapshot(root)
    assert snap is not None
    assert snap.display_name == "roundtable"
    sec = snap.agents["Security"]
    assert sec.runtime == "llm"
    assert sec.required_deps == ("Profiler_CodeMap",)
    assert sec.description == "Finds vulnerabilities."
    # A delivery label maps back to its producer for edge resolution.
    assert snap.label_to_producer()["security_intent_pack"] == "SecurityIntentProfiler"
    # to_payload → from_payload is loss-free.
    assert from_payload(to_payload(snap)) == snap
    # snapshot_to_json is valid JSON parseable back to the same snapshot.
    assert from_payload(json.loads(snapshot_to_json(snap))) == snap


def test_legacy_session_uses_graph_provider_fallback(tmp_path: Path):
    """A session with no graph.json (legacy) enriches from an injected provider;
    with neither snapshot nor provider it renders un-enriched (never assumed)."""
    root = _write_session(tmp_path / "s")
    (root / "graph.json").unlink()  # simulate a pre-snapshot session

    # No snapshot, no provider → nodes are un-enriched but still load.
    bare = load_report_model(root)
    sec_bare = next(n for n in bare.nodes if n.key == "Security")
    assert sec_bare.known_in_graph is False
    assert sec_bare.emoji == ""

    # A provider supplies the live snapshot → full enrichment returns.
    snap = from_payload({"agents": _base_snapshot_agents(), "displayName": "roundtable"})
    enriched = load_report_model(root, graph_provider=lambda: snap)
    sec = next(n for n in enriched.nodes if n.key == "Security")
    assert sec.known_in_graph is True
    assert sec.runtime == "llm"
    assert sec.emoji == "🔒"
    assert enriched.meta.tool_name == "roundtable"

"""Unit tests for the ``usage-summary.json`` analytics builder."""

from __future__ import annotations

from roundtable.backend import BillingValue, ExecutionPolicy
from roundtable.backend.usage import AgentUsage
from roundtable.engine.agent_runner.model import AgentRunOutcome, AttemptDetail
from roundtable.persistence.usage_summary import build_usage_summary


def _summary(outcomes, mcp_prewarm=None):
    return build_usage_summary(
        session_id="s1",
        outcome_label="APPROVE",
        agent_outcomes=outcomes,
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:05:00Z",
        mcp_prewarm=mcp_prewarm,
    )


def test_empty_run_is_safe() -> None:
    s = _summary({})
    assert s["sessionId"] == "s1"
    assert s["costRollup"]["agentCount"] == 0
    assert s["costRollup"]["billing"]["status"] == "not_applicable"
    assert s["perAgent"] == []
    assert s["toolUsage"]["totals"]["distinctTools"] == 0
    assert s["mcpUsage"]["servers"] == []
    assert s["anomalies"]["invalidAgents"] == []


def test_submission_status_and_corrections_are_reported_separately_from_tools() -> None:
    detail = AttemptDetail(
        attempt=1,
        model="m",
        outcome="submission_valid",
        submission_status="accepted",
        submissions=[
            {
                "status": "rejected",
                "correction": 1,
                "terminalCompletion": False,
                "diagnosticFingerprint": "abc",
            },
            {"status": "accepted", "correction": 1, "terminalCompletion": True},
        ],
    )
    outcome = AgentRunOutcome(
        agent="Judge",
        response='{"claims":[]}',
        valid=True,
        gate="all",
        submission_status="accepted",
        attempts=1,
        attempts_detail=[detail],
    )
    row = _summary({"Judge": outcome})["perAgent"][0]
    assert row["submissionStatus"] == "accepted"
    assert [item["status"] for item in row["attemptsDetail"][0]["submissions"]] == [
        "rejected",
        "accepted",
    ]
    assert row["toolCount"] == 0


def test_cost_rollup_sums_across_agents() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            wall_clock_ms_total=1500.0,
            usage=AgentUsage(
                output_tokens=10,
                rounds=1,
                duration_ms=100,
                billing=BillingValue.complete(1_000_000_000.0, 1.25),
            ),
        ),
        "Judge": AgentRunOutcome(
            agent="Judge",
            response="{}",
            valid=True,
            gate="all",
            wall_clock_ms_total=2500.0,
            usage=AgentUsage(
                output_tokens=20,
                rounds=2,
                duration_ms=200,
                session_duration_ms=3000,
                billing=BillingValue.complete(2_000_000_000.0, 2.5),
            ),
        ),
    }
    roll = _summary(outcomes)["costRollup"]
    assert roll["agentCount"] == 2
    assert roll["outputTokens"] == 30
    assert roll["billing"] == {
        "source": "copilot-sdk/session.usage.getMetrics",
        "status": "complete",
        "totalNanoAiu": 3_000_000_000.0,
        "totalPremiumRequestCost": 3.75,
    }
    assert "premiumRequests" not in roll
    assert roll["rounds"] == 3
    assert roll["apiDurationMs"] == 300.0
    assert roll["sessionDurationMs"] == 3000.0
    assert roll["wallClockMs"] == 4000.0
    assert roll["invalidCount"] == 0


def test_token_breakdown_surfaces_input_cache_total() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            usage=AgentUsage(
                output_tokens=20,
                input_tokens=500,
                cache_read_tokens=300,
                cache_write_tokens=40,
                reasoning_tokens=6,
                rounds=1,
            ),
        )
    }
    summary = _summary(outcomes)
    roll = summary["costRollup"]
    assert roll["inputTokens"] == 500
    assert roll["totalTokens"] == 520
    assert roll["cacheReadTokens"] == 300
    assert roll["cacheWriteTokens"] == 40
    assert roll["reasoningTokens"] == 6
    # per-agent row carries the same breakdown
    agent = summary["perAgent"][0]
    assert agent["inputTokens"] == 500
    assert agent["totalTokens"] == 520


def test_token_breakdown_omitted_when_not_surfaced() -> None:
    # A run with only output tokens (e.g. mock backend) keeps the compact shape.
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            usage=AgentUsage(output_tokens=20, rounds=1),
        )
    }
    roll = _summary(outcomes)["costRollup"]
    assert roll["outputTokens"] == 20
    for key in ("inputTokens", "totalTokens", "cacheReadTokens"):
        assert key not in roll


def test_tool_usage_splits_builtin_and_mcp() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            tools_used=["view", "view", "ado-repo_get_file_content"],
        ),
        "Security": AgentRunOutcome(
            agent="Security",
            response="{}",
            valid=True,
            gate="all",
            tools_used=["view", "ado-repo_get_file_content"],
        ),
    }
    # The builtin-vs-MCP split is driven by the pre-flight probe's server names.
    prewarm = [{"name": "ado", "verdict": "ready"}]
    tu = _summary(outcomes, prewarm)["toolUsage"]
    assert tu["builtin"]["view"] == {"calls": 3, "agents": 2}
    assert tu["mcp"]["ado-repo_get_file_content"] == {"calls": 2, "agents": 2}
    assert tu["totals"]["builtinCalls"] == 3
    assert tu["totals"]["mcpCalls"] == 2
    assert tu["totals"]["distinctTools"] == 2


def test_mcp_usage_lists_servers_and_invokers() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            tools_used=["ado-repo_get_file_content", "view"],
        ),
    }
    prewarm = [{"name": "ado", "verdict": "ready"}]
    mu = _summary(outcomes, prewarm)["mcpUsage"]
    assert mu["servers"][0]["name"] == "ado"
    assert mu["servers"][0]["verdict"] == "ready"
    assert mu["byAgent"]["Analyst"] == ["ado-repo_get_file_content"]


def test_attempt_retains_redacted_sdk_event_sequence_and_omission_count() -> None:
    detail = AttemptDetail(
        attempt=1,
        model="m",
        outcome="valid",
        events=[
            {
                "type": "assistant.message",
                "sensitiveValues": [
                    {"field": "content", "classification": "high", "retained": False}
                ],
            }
        ],
        events_truncated=True,
        events_omitted_count=4,
    )
    outcome = AgentRunOutcome(
        agent="Analyst",
        response="{}",
        valid=True,
        gate="all",
        attempts=1,
        attempts_detail=[detail],
    )

    attempt = _summary({"Analyst": outcome})["perAgent"][0]["attemptsDetail"][0]
    assert attempt["sdkEvents"] == detail.events
    assert attempt["sdkEventRetention"] == {"truncated": True, "omittedCount": 4}


def test_attempt_surfaces_resolved_backend_execution_policy() -> None:
    detail = AttemptDetail(
        attempt=1,
        model="m",
        outcome="valid",
        execution_policy=ExecutionPolicy(120, 30, False),
    )
    outcome = AgentRunOutcome(
        agent="redgreen",
        response="{}",
        valid=True,
        gate="all",
        attempts=1,
        attempts_detail=[detail],
    )

    attempt = _summary({"redgreen": outcome})["perAgent"][0]["attemptsDetail"][0]
    assert attempt["executionPolicy"] == {
        "shellInvocationCapSeconds": 120,
        "backgroundPollCapSeconds": 30,
        "detachedAllowed": False,
    }


def test_validation_tax_counts_rejected_ai_credit_billing_and_retries() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            attempts=2,
            usage=AgentUsage(
                output_tokens=30,
                rounds=2,
                billing=BillingValue.complete(3_000_000_000.0, 3.0),
            ),
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="ovg_reject",
                    usage=AgentUsage(
                        output_tokens=10,
                        rounds=1,
                        billing=BillingValue.complete(1_000_000_000.0, 1.0),
                    ),
                ),
                AttemptDetail(
                    attempt=2,
                    model="m",
                    outcome="valid",
                    usage=AgentUsage(
                        output_tokens=20,
                        rounds=1,
                        billing=BillingValue.complete(2_000_000_000.0, 2.0),
                    ),
                ),
            ],
        ),
    }
    tax = _summary(outcomes)["validationTax"]
    assert tax["attemptsHistogram"] == {"2": 1}
    assert tax["billingOnRejects"]["totalNanoAiu"] == 1_000_000_000.0
    assert tax["retriedAgents"] == ["Analyst"]


def test_unavailable_billing_is_structured_and_does_not_warn_for_mock_usage() -> None:
    unavailable = AgentRunOutcome(
        agent="Live",
        response="{}",
        valid=True,
        gate="all",
        attempts=1,
        usage=AgentUsage(rounds=1, billing=BillingValue.unavailable()),
        attempts_detail=[
            AttemptDetail(
                attempt=1,
                model="m",
                outcome="valid",
                usage=AgentUsage(rounds=1, billing=BillingValue.unavailable()),
            )
        ],
    )
    mock = AgentRunOutcome(
        agent="Mock",
        response="{}",
        valid=True,
        gate="all",
    )
    summary = _summary({"Live": unavailable, "Mock": mock})
    assert summary["costRollup"]["billing"]["status"] == "unavailable"
    assert summary["anomalies"]["billingWarnings"] == [
        {
            "agent": "Live",
            "attempt": 1,
            "source": "copilot-sdk/session.usage.getMetrics",
            "warningCode": "sdk_usage_metrics_unavailable",
        }
    ]
    mock_row = next(row for row in summary["perAgent"] if row["agent"] == "Mock")
    assert mock_row["billing"]["status"] == "not_applicable"


def test_anomalies_flag_invalid_fallback_and_timeout() -> None:
    outcomes = {
        "Broken": AgentRunOutcome(
            agent="Broken",
            response="",
            valid=False,
            gate=None,
            last_error="timeout",
            model="m1",
        ),
    }
    an = _summary(outcomes)["anomalies"]
    assert an["invalidAgents"] == ["Broken"]
    assert an["timedOutAgents"] == ["Broken"]


def test_anomalies_flag_truncated_or_filtered_agents() -> None:
    outcomes = {
        "Cut": AgentRunOutcome(
            agent="Cut",
            response="{}",
            valid=True,
            gate="all",
            usage=AgentUsage(output_tokens=10, rounds=1, finish_reasons=("stop", "length")),
        ),
        "Clean": AgentRunOutcome(
            agent="Clean",
            response="{}",
            valid=True,
            gate="all",
            usage=AgentUsage(output_tokens=10, rounds=1, finish_reasons=("stop",)),
        ),
    }
    an = _summary(outcomes)["anomalies"]
    assert an["truncatedOrFilteredAgents"] == [{"agent": "Cut", "reasons": ["length"]}]


def test_attempt_detail_surfaces_redacted_tool_calls() -> None:
    # Each attempt's arg-bearing toolCalls surface on the perAgent row.
    outcomes = {
        "Security": AgentRunOutcome(
            agent="Security",
            response="{}",
            valid=True,
            gate="all",
            attempts=1,
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="valid",
                    tools_invoked=["view", "rg"],
                    tool_calls=[
                        {"name": "view", "args": {"path": "src/a.py"}},
                        {
                            "name": "rg",
                            "args": {"pattern": "TODO"},
                            "ok": False,
                            "toolError": {
                                "source": "sdk-host",
                                "type": "ToolTimeout",
                                "code": "TOOL_TIMEOUT",
                                "message": "deadline exceeded",
                            },
                        },
                    ],
                ),
            ],
        ),
    }
    ad = _summary(outcomes)["perAgent"][0]["attemptsDetail"][0]
    assert "toolsInvoked" not in ad
    assert ad["toolCalls"] == [
        {"name": "view", "args": {"path": "src/a.py"}},
        {
            "name": "rg",
            "args": {"pattern": "TODO"},
            "ok": False,
            "toolError": {
                "source": "sdk-host",
                "type": "ToolTimeout",
                "code": "TOOL_TIMEOUT",
                "message": "deadline exceeded",
            },
        },
    ]


def test_attempt_detail_surfaces_timeout_phase_and_safe_active_snapshot() -> None:
    outcomes = {
        "RedGreen": AgentRunOutcome(
            agent="RedGreen",
            response="",
            valid=False,
            gate=None,
            attempts=1,
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="api_error",
                    timeout_phase="background_tool_wait",
                    timeout_snapshot=[
                        {
                            "name": "read_powershell",
                            "shellId": "sh-1",
                            "activeAtTimeout": True,
                            "args": {"command": "secret"},
                        }
                    ],
                )
            ],
        )
    }
    ad = _summary(outcomes)["perAgent"][0]["attemptsDetail"][0]
    assert ad["timeoutPhase"] == "background_tool_wait"
    assert ad["timeoutSnapshot"] == [
        {
            "name": "read_powershell",
            "shellId": "sh-1",
            "activeAtTimeout": True,
            "argumentRetention": {"dropped": {"command": "sensitive"}},
        }
    ]


def test_accepts_plain_mapping_shape() -> None:
    # Mirrors trace.py dual-shape tolerance: mapping values must work too.
    outcomes = {
        "Analyst": {
            "agent": "Analyst",
            "valid": True,
            "attempts": 1,
            "wallClockMs": 900.0,
            "toolsUsed": ["view"],
        }
    }
    s = _summary(outcomes)
    assert s["perAgent"][0]["agent"] == "Analyst"
    assert s["perAgent"][0]["wallClockMs"] == 900.0
    assert s["toolUsage"]["builtin"]["view"]["calls"] == 1


def test_attempt_row_retains_safe_retry_feedback() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            attempts=2,
            attempts_detail=[
                AttemptDetail(attempt=1, model="m", outcome="submission_missing"),
                AttemptDetail(
                    attempt=2,
                    model="m",
                    outcome="submission_valid",
                    retry_feedback="OUTPUT NOT SUBMITTED — call roundtable_submit_output.",
                ),
            ],
        )
    }

    attempts = _summary(outcomes)["perAgent"][0]["attemptsDetail"]
    assert attempts[1]["retryFeedback"] == ("OUTPUT NOT SUBMITTED — call roundtable_submit_output.")

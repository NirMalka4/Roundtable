"""Unit tests for persistence.trace — slim artifacts, atomic writes, OVG-fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtable.backend import ExecutionPolicy, RunResult
from roundtable.backend.usage import EMPTY_USAGE, AgentUsage, BillingValue
from roundtable.engine.agent_runner import AgentRunOutcome, AttemptDetail
from roundtable.persistence.trace import (
    build_trace,
    persist_session,
    render_cost_footer,
    write_atomic,
)
from tests.support.engine import run_agent_with_ovg


def test_write_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_atomic(target, '{"a":1}')
    assert target.read_text(encoding="utf-8") == '{"a":1}'
    # The temp sibling must not survive a successful write.
    assert not (tmp_path / "out.json.tmp").exists()


def test_submission_observability_is_separate_and_does_not_duplicate_payload() -> None:
    outcome = AgentRunOutcome(
        agent="Judge",
        response='{"verdict":{"label":"APPROVE"},"claims":[]}',
        valid=True,
        gate="all",
        attempts=1,
        submission_status="accepted",
        attempts_detail=[
            AttemptDetail(
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
                    {
                        "status": "accepted",
                        "correction": 1,
                        "terminalCompletion": True,
                    },
                ],
            )
        ],
    )
    agent = build_trace(session_id="s", agent_outcomes={"Judge": outcome})["agents"][0]
    assert agent["submissionStatus"] == "accepted"
    attempt = agent["attemptsDetail"][0]
    assert attempt["submissionStatus"] == "accepted"
    assert [call["status"] for call in attempt["submissions"]] == ["rejected", "accepted"]
    assert "output" not in json.dumps(attempt["submissions"])


def test_rejected_submission_redacts_secret_values_and_deduplicates_diagnostics(
    inspectorx_config,
) -> None:
    secret = "submission-secret-value-7c04"

    class Backend:
        def run(self, request):
            assert request.submission is not None
            request.submission.submit({"secret": secret})
            return RunResult(
                final_content="",
                tool_call_count=1,
                rounds=1,
                exit_code=0,
                submission=request.submission.snapshot(),
            )

    outcome = run_agent_with_ovg(
        agent="Profiler_Intent",
        context="ctx",
        backend=Backend(),
        configuration=inspectorx_config,
        max_attempts=1,
    )
    trace = build_trace(session_id="s", agent_outcomes={"Profiler_Intent": outcome})
    persisted = json.dumps(trace, sort_keys=True)

    assert secret not in persisted
    attempt = trace["agents"][0]["attemptsDetail"][0]
    assert attempt["errors"]
    assert all("actual" not in diagnostic for diagnostic in attempt["errors"])
    assert all("snippet" not in diagnostic for diagnostic in attempt["errors"])
    assert all(
        "diagnostics" not in submission
        and "warnings" not in submission
        and "feedback" not in submission
        for submission in attempt["submissions"]
    )
    assert any(
        diagnostic.get("gate") == "json_schema"
        and diagnostic.get("keyword") == "required"
        and diagnostic.get("message") == "a required property is missing"
        and "intent_profile" in diagnostic.get("expected", "")
        for diagnostic in attempt["errors"]
    )


def test_persist_session_writes_artifacts_and_exit_code(tmp_path: Path, inspectorx_config) -> None:
    outcomes = {
        "Judge": AgentRunOutcome(
            agent="Judge", response='{"verdict":"REJECT"}', valid=True, gate="all"
        ),
    }
    result = persist_session(
        tmp_path,
        session_id="sess_x",
        agent_outcomes=outcomes,
        overlay={"verdict": "REJECT", "counts": {"blocking": 1, "all": 1}},
        report_md="# Review verdict: REJECT\n\n## Findings\n- Blocking: 1\n",
        report_filename="verdict.md",
        exit_code=1,
        log_lines=["[run] started", "[run] done"],
        configuration=inspectorx_config,
    )
    assert result.session_dir == tmp_path / "sess_x"
    assert result.trace_path.exists() and result.report_path.exists()
    assert result.exit_code == 1  # passed in by the caller, not derived
    configuration = json.loads(
        (result.session_dir / "configuration.json").read_text(encoding="utf-8")
    )
    assert configuration["kind"] == "shipped"
    assert configuration["bundle"] == "inspectorx"

    trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
    assert trace["sessionId"] == "sess_x"
    # The opaque overlay is merged verbatim right after the session id.
    assert trace["verdict"] == "REJECT"
    assert trace["counts"]["blocking"] == 1

    md = result.report_path.read_text(encoding="utf-8")
    assert "REJECT" in md and "Blocking: 1" in md

    log = result.log_path.read_text(encoding="utf-8")
    assert "started" in log and "done" in log


def test_persist_session_exit_code_passthrough(tmp_path: Path) -> None:
    outcomes = {"Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all")}
    result = persist_session(
        tmp_path,
        session_id="sess_clean",
        agent_outcomes=outcomes,
        exit_code=0,
    )
    assert result.exit_code == 0  # the core echoes the caller-supplied code


def test_persist_session_writes_git_context_when_supplied(tmp_path: Path) -> None:
    outcomes = {"Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all")}
    gc = {
        "mode": "clone-worktree",
        "snapshotSha": "abc123",
        "baseSha": "def456",
        "addDirs": ["/w"],
        "cwd": "/w",
    }
    result = persist_session(
        tmp_path,
        session_id="sess_gc",
        agent_outcomes=outcomes,
        git_context=gc,
    )
    gc_path = result.session_dir / "git-context.json"
    assert gc_path.exists()
    assert json.loads(gc_path.read_text(encoding="utf-8")) == gc


def test_persist_session_no_git_context_writes_no_file(tmp_path: Path) -> None:
    outcomes = {"Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all")}
    result = persist_session(
        tmp_path,
        session_id="sess_nogc",
        agent_outcomes=outcomes,
    )
    assert not (result.session_dir / "git-context.json").exists()


def test_build_trace_merges_opaque_overlay() -> None:
    # The persistence core neither produces nor interprets the overlay — it merges
    # whatever mapping the caller hands in verbatim, right after the session id.
    outcomes = {"Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all")}
    overlay = {"verdict": "APPROVE", "counts": {"blocking": 0}, "domainField": {"x": 1}}
    trace = build_trace(session_id="s", agent_outcomes=outcomes, overlay=overlay)
    assert trace["verdict"] == "APPROVE"
    assert trace["counts"] == {"blocking": 0}
    assert trace["domainField"] == {"x": 1}
    # Neutral run-record keys still present alongside the merged overlay.
    assert trace["sessionId"] == "s"
    assert trace["agentCount"] == 1


def test_build_trace_omits_overlay_keys_when_absent() -> None:
    outcomes = {"Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all")}
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    # No overlay ⇒ no domain keys leak into the neutral record.
    assert "verdict" not in trace
    assert "counts" not in trace
    assert "subject" not in trace


def test_persist_session_writes_no_index(tmp_path: Path) -> None:
    # The per-repo derived index is a DOMAIN concern owned by the caller (review
    # shell); the persistence core never writes it.
    outcomes = {"Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all")}
    persist_session(
        tmp_path,
        session_id="sess_x",
        agent_outcomes=outcomes,
        overlay={"subject": {"repo": "MyRepo"}},
    )
    assert not (tmp_path / "index.json").exists()


# ── usage persistence + cost footer ──────────────────────────────────────────


def test_build_trace_omits_usage_when_no_usage() -> None:
    # No AgentUsage on the outcomes ⇒ no per-agent or session usage keys, keeping
    # the no-usage trace.json byte-stable.
    outcomes = {
        "Analyst": AgentRunOutcome(agent="Analyst", response="{}", valid=True, gate="all"),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    assert "usage" not in trace
    assert "usage" not in trace["agents"][0]


def test_build_trace_records_per_agent_and_session_usage() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            usage=AgentUsage(
                output_tokens=10,
                rounds=1,
                billing=BillingValue.complete(1_000_000_000.0, 1.25),
            ),
        ),
        "Judge": AgentRunOutcome(
            agent="Judge",
            response="{}",
            valid=True,
            gate="all",
            usage=AgentUsage(
                output_tokens=20,
                rounds=2,
                billing=BillingValue.complete(2_000_000_000.0, 2.5),
            ),
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    # Session rollup = sum of per-agent usage.
    assert trace["usage"]["outputTokens"] == 30
    assert trace["usage"]["billing"] == {
        "source": "copilot-sdk/session.usage.getMetrics",
        "status": "complete",
        "totalNanoAiu": 3_000_000_000.0,
        "totalPremiumRequestCost": 3.75,
    }
    assert "premiumRequests" not in trace["usage"]
    assert trace["usage"]["rounds"] == 3
    analyst = next(a for a in trace["agents"] if a["agent"] == "Analyst")
    assert analyst["usage"]["outputTokens"] == 10


def test_build_trace_persists_attempt_billing_provenance_and_warning() -> None:
    detail = AttemptDetail(
        attempt=1,
        model="m",
        outcome="api_error",
        usage=AgentUsage(rounds=1, billing=BillingValue.unavailable()),
        warnings=[
            {
                "kind": "sdk_usage_metrics_unavailable",
                "message": "SDK usage metrics unavailable; AI Credit reporting omitted.",
            }
        ],
    )
    outcome = AgentRunOutcome(
        agent="Analyst",
        response="",
        valid=False,
        gate=None,
        attempts=1,
        usage=detail.usage,
        attempts_detail=[detail],
    )
    attempt = build_trace(session_id="s", agent_outcomes={"Analyst": outcome})["agents"][0][
        "attemptsDetail"
    ][0]
    assert attempt["usage"]["billing"] == {
        "source": "copilot-sdk/session.usage.getMetrics",
        "status": "unavailable",
        "warningCode": "sdk_usage_metrics_unavailable",
    }
    assert attempt["warnings"][0] == {
        "kind": "sdk_usage_metrics_unavailable",
        "message": "backend diagnostic 'sdk_usage_metrics_unavailable'",
    }


def test_build_trace_omits_ado_fields_when_no_mcp() -> None:
    # No tools_used / mcp_servers ⇒ no toolStats/mcpServers keys anywhere,
    # keeping the tool-free trace.json byte-stable.
    outcomes = {
        "Analyst": AgentRunOutcome(agent="Analyst", response="{}", valid=True, gate="all"),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    assert "mcpServers" not in trace
    a0 = trace["agents"][0]
    assert "toolStats" not in a0
    assert "mcpServers" not in a0


def test_build_trace_persists_warnings_when_present() -> None:
    # Run-level notices (structured {kind,message}) are surfaced in trace.
    notices = [
        {"kind": "session_adopt", "message": "Adopted the backend session id."},
        {"kind": "abort", "message": "Failed to produce valid output after 3 attempts."},
    ]
    outcomes = {
        "Judge": AgentRunOutcome(
            agent="Judge",
            response="{}",
            valid=True,
            gate="all",
            warnings=notices,
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    a0 = trace["agents"][0]
    assert a0["warnings"] == notices


def test_build_trace_omits_warnings_when_empty() -> None:
    outcomes = {
        "Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all"),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    assert "warnings" not in trace["agents"][0]


def test_build_trace_records_per_agent_tool_stats() -> None:
    # toolStats counts every invoked tool (first-seen order), MCP names kept
    # namespaced so builtin/MCP can be split downstream.
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            tools_used=[
                "view",
                "ado-repo_get_repository_by_name",
                "grep",
                "ado-repo_get_repository_by_name",
            ],
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    a0 = trace["agents"][0]
    assert a0["toolStats"] == {
        "view": 1,
        "ado-repo_get_repository_by_name": 2,
        "grep": 1,
    }


def test_build_trace_never_emits_mcp_servers() -> None:
    # SDK-only: there is no runtime source for per-agent/session MCP-server load
    # status, so the dead `mcpServers` plumbing was removed. Even when an outcome
    # still carries `mcp_servers`, trace.json must not surface it — MCP health now
    # lives solely in `mcpPrewarm`.
    srv = {"name": "ado", "status": "connected", "transport": "stdio"}
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst", response="{}", valid=True, gate="all", mcp_servers=[srv]
        ),
        "Judge": AgentRunOutcome(
            agent="Judge", response="{}", valid=True, gate="all", mcp_servers=[srv]
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    assert "mcpServers" not in trace
    assert all("mcpServers" not in a for a in trace["agents"])


def test_build_trace_records_mcp_prewarm_when_supplied() -> None:
    outcomes = {"Analyst": AgentRunOutcome(agent="Analyst", response="{}", valid=True, gate="all")}
    prewarm = [
        {"name": "ado-work-items", "verdict": "ready", "pruned": False},
        {"name": "kusto-mcp", "verdict": "unreachable", "pruned": True},
    ]
    trace = build_trace(session_id="s", agent_outcomes=outcomes, mcp_prewarm=prewarm)
    assert trace["mcpPrewarm"] == prewarm


def test_build_trace_omits_mcp_prewarm_when_empty() -> None:
    outcomes = {"Analyst": AgentRunOutcome(agent="Analyst", response="{}", valid=True, gate="all")}
    # Absent and empty-list both mean "nothing probed" → byte-stable omission.
    assert "mcpPrewarm" not in build_trace(session_id="s", agent_outcomes=outcomes)
    assert "mcpPrewarm" not in build_trace(session_id="s", agent_outcomes=outcomes, mcp_prewarm=[])


def test_render_cost_footer_empty_when_no_usage() -> None:
    assert render_cost_footer(EMPTY_USAGE) == ""


def test_render_cost_footer_has_totals() -> None:
    footer = render_cost_footer(
        AgentUsage(
            output_tokens=200,
            rounds=3,
            billing=BillingValue.complete(1_500_000_000.0, 1.5),
        )
    )
    assert "## Cost" in footer
    assert "200" in footer  # output tokens, thousands-separated
    assert "AI Credits consumed: 1.5" in footer
    assert "Premium requests" not in footer
    assert "advisory" in footer  # the non-parity disclaimer


def test_render_cost_footer_includes_input_and_cache_when_present() -> None:
    footer = render_cost_footer(
        AgentUsage(
            output_tokens=200,
            input_tokens=5000,
            cache_read_tokens=1200,
            reasoning_tokens=50,
            rounds=3,
            billing=BillingValue.complete(1_500_000_000.0, 1.5),
        )
    )
    assert "Input tokens: 5,000" in footer
    assert "of which cached: 1,200" in footer
    assert "Total tokens: 5,200" in footer
    assert "of which reasoning: 50" in footer


def test_render_cost_footer_omits_input_lines_when_unmeasured() -> None:
    footer = render_cost_footer(AgentUsage(output_tokens=200, rounds=1))
    assert "Input tokens" not in footer
    assert "Total tokens" not in footer


def test_render_cost_footer_omits_incomplete_billing() -> None:
    footer = render_cost_footer(
        AgentUsage(
            output_tokens=200,
            rounds=1,
            billing=BillingValue.unavailable(),
        )
    )
    assert "AI Credits" not in footer
    assert "Premium" not in footer


def test_render_cost_footer_flags_abnormal_stop() -> None:
    footer = render_cost_footer(
        AgentUsage(output_tokens=200, rounds=1, finish_reasons=("stop", "length"))
    )
    assert "Abnormal stop: length" in footer


def test_render_cost_footer_no_abnormal_line_when_benign() -> None:
    footer = render_cost_footer(
        AgentUsage(output_tokens=200, rounds=1, finish_reasons=("stop", "tool_calls"))
    )
    assert "Abnormal stop" not in footer


def test_persist_session_appends_cost_footer_when_usage_present(tmp_path: Path) -> None:
    outcomes = {
        "Judge": AgentRunOutcome(
            agent="Judge",
            response="{}",
            valid=True,
            gate="all",
            usage=AgentUsage(
                output_tokens=50,
                rounds=1,
                billing=BillingValue.complete(1_000_000_000.0, 1.0),
            ),
        ),
    }
    result = persist_session(
        tmp_path,
        session_id="s",
        agent_outcomes=outcomes,
    )
    md = result.report_path.read_text(encoding="utf-8")
    assert "## Cost" in md
    assert "AI Credits consumed: 1" in md
    trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
    assert trace["usage"]["outputTokens"] == 50


def test_persist_session_no_footer_when_no_usage(tmp_path: Path) -> None:
    outcomes = {"Judge": AgentRunOutcome(agent="Judge", response="{}", valid=True, gate="all")}
    result = persist_session(
        tmp_path,
        session_id="s2",
        agent_outcomes=outcomes,
    )
    md = result.report_path.read_text(encoding="utf-8")
    assert "## Cost" not in md


# ── per-attempt OVG observability in trace ───────────────────────────────────


def test_build_trace_omits_attempts_detail_on_clean_single_pass() -> None:
    # A first-try valid agent ⇒ no attemptsDetail key (byte-stable happy path),
    # even though a single 'valid' attempt record exists.
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            attempts=1,
            attempts_detail=[AttemptDetail(attempt=1, model="m", outcome="valid")],
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    assert "attemptsDetail" not in trace["agents"][0]


def test_build_trace_emits_attempts_detail_when_valid_pass_carries_warnings() -> None:
    # A clean single pass that nonetheless collected an advisory OVG warning must
    # still emit attemptsDetail — otherwise that structured warning would vanish
    # (the happy-path omission only applies to a warning-free valid attempt).
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            attempts=1,
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="valid",
                    warnings=[{"gate": "generic_phrase", "message": "boilerplate"}],
                )
            ],
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    detail = trace["agents"][0]["attemptsDetail"]
    assert detail[0]["warnings"][0]["gate"] == "generic_phrase"
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            attempts=2,
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="ovg_reject",
                    errors=[
                        {
                            "gate": "schema",
                            "path": "locations",
                            "message": "missing field locations",
                        }
                    ],
                ),
                AttemptDetail(attempt=2, model="m", outcome="valid"),
            ],
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    detail = trace["agents"][0]["attemptsDetail"]
    assert [d["outcome"] for d in detail] == ["ovg_reject", "valid"]
    assert detail[0]["errors"][0]["gate"] == "schema"
    assert detail[0]["errors"][0]["message"] == "output failed gate 'schema'"
    # The second (clean) attempt carries no errors key.
    assert "errors" not in detail[1]


@pytest.mark.parametrize("as_mapping", (False, True), ids=("object", "mapping"))
@pytest.mark.parametrize("diagnostic_key", ("errors", "warnings"))
def test_build_trace_retains_only_candidate_safe_diagnostics(
    as_mapping: bool, diagnostic_key: str
) -> None:
    diagnostic = {
        "gate": "json_schema",
        "path": "findings[0].severity",
        "keyword": "enum",
        "expected": "low | medium | high | critical",
        "description": "Canonical finding severity.",
        "location": "line 4, column 17",
        "message": "bad enum: HUGE",
        "snippet": '"HUGE"',
        "value": "HUGE",
    }
    detail_values = {
        "attempt": 1,
        "model": "m",
        "outcome": "ovg_reject",
        diagnostic_key: [diagnostic],
    }
    detail = detail_values if as_mapping else AttemptDetail(**detail_values)
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=False,
            gate="all",
            attempts=1,
            attempts_detail=[detail],
        ),
    }

    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    retained = trace["agents"][0]["attemptsDetail"][0][diagnostic_key][0]

    assert retained == {
        "gate": "json_schema",
        "path": "findings[0].severity",
        "keyword": "enum",
        "expected": "low | medium | high | critical",
        "description": "Canonical finding severity.",
        "location": "line 4, column 17",
        "message": "value is not in the allowed set",
    }
    assert "HUGE" not in str(retained)


def test_build_trace_emits_retry_feedback_when_present_and_omits_when_empty() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            attempts=2,
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="ovg_reject",
                    errors=[{"gate": "schema", "message": "missing field locations"}],
                ),
                AttemptDetail(
                    attempt=2,
                    model="m",
                    outcome="valid",
                    retry_feedback="YOUR OUTPUT FAILED VALIDATION:\n  - missing field locations",
                ),
            ],
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    detail = trace["agents"][0]["attemptsDetail"]
    # Attempt 1 (no prior failure) carries no retryFeedback key.
    assert "retryFeedback" not in detail[0]
    # Attempt 2 was steered by the prior failure — its feedback is persisted.
    assert detail[1]["retryFeedback"] == (
        "YOUR OUTPUT FAILED VALIDATION:\n  - missing field locations"
    )


def test_build_trace_emits_attempts_detail_when_invalid_single_attempt() -> None:
    # A model-level error that stops after one attempt (no failover left) ends
    # invalid ⇒ attemptsDetail is emitted despite attempts == 1.
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="",
            valid=False,
            gate=None,
            attempts=1,
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="model_error",
                    errors=[{"kind": "model_error", "message": "model m unavailable"}],
                ),
            ],
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    detail = trace["agents"][0]["attemptsDetail"]
    assert detail[0]["outcome"] == "model_error"
    assert detail[0]["errors"][0]["kind"] == "model_error"
    assert detail[0]["errors"][0]["message"] == "backend diagnostic 'model_error'"


def test_build_trace_attempt_detail_emits_per_attempt_usage() -> None:
    outcomes = {
        "Analyst": AgentRunOutcome(
            agent="Analyst",
            response="{}",
            valid=True,
            gate="all",
            attempts=2,
            usage=AgentUsage(output_tokens=30, rounds=2),
            attempts_detail=[
                AttemptDetail(
                    attempt=1,
                    model="m",
                    outcome="ovg_reject",
                    errors=[{"gate": "schema", "message": "bad"}],
                    tools_invoked=["view"],
                    tool_calls=[
                        {
                            "name": "rg",
                            "ok": False,
                            "durationMs": 20_100,
                            "toolError": {
                                "source": "sdk-host",
                                "type": "ToolTimeout",
                                "code": "TOOL_TIMEOUT",
                                "message": "deadline exceeded",
                            },
                        }
                    ],
                    wall_clock_ms=1200.0,
                    usage=AgentUsage(output_tokens=10, rounds=1),
                ),
                AttemptDetail(
                    attempt=2,
                    model="m",
                    outcome="valid",
                    usage=AgentUsage(output_tokens=20, rounds=1),
                ),
            ],
        ),
    }
    trace = build_trace(session_id="s", agent_outcomes=outcomes)
    detail = trace["agents"][0]["attemptsDetail"]
    assert detail[0]["usage"]["outputTokens"] == 10
    assert detail[0]["toolsInvoked"] == ["view"]
    assert detail[0]["toolErrors"] == [
        {
            "name": "rg",
            "toolError": {
                "source": "sdk-host",
                "type": "ToolTimeout",
                "code": "TOOL_TIMEOUT",
                "message": "deadline exceeded",
            },
            "durationMs": 20_100,
        }
    ]
    assert detail[0]["wallClockMs"] == 1200.0
    assert detail[1]["usage"]["outputTokens"] == 20
    # Per-attempt usages sum to the agent aggregate emitted in the same entry.
    summed = sum(d["usage"]["outputTokens"] for d in detail)
    assert summed == trace["agents"][0]["usage"]["outputTokens"] == 30


def _trace_agent(**kw) -> dict:
    outcome = AgentRunOutcome(agent="A", response="{}", valid=True, gate="all", **kw)
    trace = build_trace(session_id="s", agent_outcomes={"A": outcome})
    return next(a for a in trace["agents"] if a["agent"] == "A")


def test_trace_surfaces_execution_policy_on_first_try_success() -> None:
    detail = AttemptDetail(
        attempt=1,
        model="m",
        outcome="valid",
        execution_policy=ExecutionPolicy(120, 30, False),
    )
    agent = _trace_agent(attempts=1, attempts_detail=[detail])
    assert agent["attemptsDetail"][0]["executionPolicy"] == {
        "shellInvocationCapSeconds": 120,
        "backgroundPollCapSeconds": 30,
        "detachedAllowed": False,
    }


def test_trace_surfaces_backend_outcome_on_a_passing_attempt() -> None:
    # A turn that succeeded despite a faulting backend process must stay
    # distinguishable from a clean one, or the fault is unrecoverable after the run.
    detail = AttemptDetail(
        attempt=1,
        model="m",
        outcome="submission_valid",
        backend_outcome="session_error",
    )
    agent = _trace_agent(attempts=1, attempts_detail=[detail])
    assert agent["attemptsDetail"][0]["backendOutcome"] == "session_error"


def test_trace_omits_backend_outcome_on_a_clean_attempt() -> None:
    detail = AttemptDetail(
        attempt=1, model="m", outcome="submission_valid", submission_status="accepted"
    )
    agent = _trace_agent(attempts=1, attempts_detail=[detail])
    assert "backendOutcome" not in agent["attemptsDetail"][0]


def test_persist_session_writes_source_payloads_verbatim(tmp_path: Path) -> None:
    source_payloads = {
        "ReviewDiff": "diff --git a/x.py b/x.py\n+line\n",
        "GitHistory": "abc123 message\n",
    }
    result = persist_session(
        tmp_path,
        session_id="s",
        agent_outcomes={},
        source_payloads=source_payloads,
    )

    persisted = json.loads(
        (result.session_dir / "source-payloads.json").read_text(encoding="utf-8")
    )
    assert persisted == source_payloads


def test_persist_session_writes_versioned_replay_context_atomically(tmp_path: Path) -> None:
    replay_context = {
        "version": 1,
        "repository": {"name": "Repo"},
        "workspaceOverlay": None,
    }
    result = persist_session(
        tmp_path,
        session_id="s",
        agent_outcomes={},
        replay_context=replay_context,
    )

    path = result.session_dir / "replay-context.json"
    assert json.loads(path.read_text(encoding="utf-8")) == replay_context
    assert not path.with_suffix(".json.tmp").exists()

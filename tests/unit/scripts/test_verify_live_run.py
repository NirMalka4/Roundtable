"""Synthetic-session unit tests for the live-run acceptance grader.

These grade hand-built ``trace.json`` sessions (no LLM, no 26-agent run) so the
GATE checks that the zero-token ``--simulate`` fixture cannot exercise — exit
mapping and the ADO-MCP connectivity gate stay covered fast and
deterministically.

The grader lives in :mod:`roundtable.review.acceptance`; ``scripts/verify_live_run.py``
is a thin CLI over it. T0 (prompt parity) and T3 (TS verdict ladder) were dropped
by the consolidated grader, so their ``--reference`` tests are gone.
"""

from __future__ import annotations

import json
from pathlib import Path

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration
from roundtable.review import acceptance as vlr

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def _write_session(
    tmp: Path,
    *,
    verdict="APPROVE",
    blocking=0,
    overridden=False,
    prewarm=None,
    ado_tools_for=None,
) -> Path:
    """Write a minimal, roster-complete, conformant session dir.

    ``prewarm`` (list of {name, verdict}) populates the top-level ``mcpPrewarm``
    pre-flight probe; ``ado_tools_for`` (agent→list[str]) attaches per-agent
    ``toolStats`` entries for those tools — both for the ADO-MCP connectivity gate.
    """
    session = tmp / "session_x"
    session.mkdir(parents=True, exist_ok=True)
    ado_tools_for = ado_tools_for or {}
    agents = []
    for key in vlr.roster(CONFIGURATION):
        resp = (
            json.dumps({"verdict": verdict, "verdict_overlay": []})
            if key == "Judge"
            else json.dumps({"findings": []})
        )
        entry = {
            "agent": key,
            "valid": True,
            "gate": "semantic",
            "attempts": 1,
            "response": resp,
        }
        if key in ado_tools_for:
            entry["toolStats"] = dict.fromkeys(ado_tools_for[key], 1)
        agents.append(entry)
    trace = {
        "verdict": verdict,
        "verdictOverridden": overridden,
        "counts": {"blocking": blocking, "nonBlocking": 0, "all": blocking, "security": 0},
        "agentCount": len(agents),
        "agents": agents,
    }
    if prewarm is not None:
        trace["mcpPrewarm"] = prewarm
    (session / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    (session / "verdict.md").write_text(f"# Review verdict: {verdict}\n", encoding="utf-8")
    (session / "configuration.json").write_text(
        json.dumps(CONFIGURATION.identity), encoding="utf-8"
    )
    return session


def test_clean_session_passes_all_gates(tmp_path):
    session = _write_session(tmp_path)
    report = vlr.grade(session, observed_exit=0)
    assert not report.gate_failed
    by = {c.check: c for c in report.checks}
    assert by["verdict-valid"].status == "PASS"
    assert by["exit-mapping"].status == "PASS"
    assert by["roster"].status == "PASS"


def test_blocking_finding_without_reject_fails_gate(tmp_path):
    session = _write_session(tmp_path, verdict="APPROVE", blocking=2)
    report = vlr.grade(session, observed_exit=0)
    crit = next(c for c in report.checks if c.check == "critical-forces-reject")
    assert crit.status == "FAIL"
    assert report.gate_failed


def test_exit_mapping_mismatch_fails(tmp_path):
    session = _write_session(tmp_path, verdict="APPROVE")
    report = vlr.grade(session, observed_exit=1)  # APPROVE must be 0
    em = next(c for c in report.checks if c.check == "exit-mapping")
    assert em.status == "FAIL"
    assert report.gate_failed


# --- ADO-MCP connectivity gate -------------------------------------------


def _ado_check(report):
    return next(c for c in report.checks if c.check == "ado-mcp-connectivity")


def test_ado_mcp_not_requested_is_na(tmp_path):
    # No mcpPrewarm, no toolStats → NA, never a gate fail.
    session = _write_session(tmp_path)
    report = vlr.grade(session, observed_exit=0)
    c = _ado_check(report)
    assert c.status == "NA"
    assert not report.gate_failed


def test_ado_mcp_connected_passes(tmp_path):
    session = _write_session(
        tmp_path,
        prewarm=[{"name": "ado-work-items", "verdict": "ready"}],
        ado_tools_for={"Analyst_Logic": ["ado-work-items-repo_get_pull_request_by_id"]},
    )
    report = vlr.grade(session, observed_exit=0)
    c = _ado_check(report)
    assert c.status == "PASS"
    assert not report.gate_failed


def test_ado_mcp_present_not_connected_fails_gate(tmp_path):
    session = _write_session(
        tmp_path,
        prewarm=[{"name": "ado-work-items", "verdict": "unreachable"}],
    )
    report = vlr.grade(session, observed_exit=0)
    c = _ado_check(report)
    assert c.status == "FAIL"
    assert report.gate_failed


def test_ado_mcp_invoked_without_connected_server_fails_gate(tmp_path):
    # Agent called ado-repo_* but no healthy ado server in trace → FAIL.
    session = _write_session(
        tmp_path,
        ado_tools_for={"Analyst_Logic": ["ado-repo_list_pull_request_threads"]},
    )
    report = vlr.grade(session, observed_exit=0)
    c = _ado_check(report)
    assert c.status == "FAIL"
    assert report.gate_failed


def test_ado_mcp_accepts_warmed_slow(tmp_path):
    session = _write_session(
        tmp_path,
        prewarm=[{"name": "ado-work-items", "verdict": "warmed_slow"}],
    )
    report = vlr.grade(session, observed_exit=0)
    assert _ado_check(report).status == "PASS"

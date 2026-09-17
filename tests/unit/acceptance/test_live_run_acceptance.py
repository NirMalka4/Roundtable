"""Acceptance gate: the consolidated live-run grader over a zero-token run.

Single CI home for "is one execution acceptable?". Replaces the retired
``.live-ab/ab_gate.py`` and exercises the same grader (:mod:`roundtable.review.acceptance`)
that ``scripts/verify_live_run.py`` runs over live sessions.

* ``test_simulate_session_is_accepted`` — drive the REAL scheduler + injection + OVG
  with the mock LLM (the ``--simulate`` path: full graph, no tokens), then
  grade the produced session through T1+T2 and assert ACCEPTED. Names each high-value
  check so a regression points at the offender.
* ``test_critical_forces_reject_*`` — the grader's verdict invariant (T1
  critical-finding-forces-REJECT), pinned directly over synthetic traces so the
  PASS/FAIL/NA branches are each covered in isolation.
* ``test_simulate_review_persists_counts`` /
  ``test_blocking_finding_gives_critical_check_teeth`` — regression guard for the
  counts-threading fix: ``run_review`` now persists a ``counts`` block into the
  trace (it previously did not — counts were computed only on the publish path),
  so the T1 critical-finding-forces-REJECT branch has teeth on a review trace
  rather than always adjudicating NA.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from roundtable.backend.mock_runner import (
    build_valid_stub,
    make_mock_run_agent,
)
from roundtable.bundle import resolve_bundle
from roundtable.graph import Configuration, Edge, GraphEntry, Predicate, get_configuration
from roundtable.review.acceptance import (
    FAIL,
    NA,
    PASS,
    Report,
    check_t1,
    format_table,
    grade,
    roster,
)
from roundtable.review.flow import run_review

CONFIGURATION = get_configuration(resolve_bundle("inspectorx"))


def test_simulate_session_is_accepted(tmp_path: Path) -> None:
    """A full mock (zero-token) review must pass every GATE tier (T1 + T2)."""
    result = run_review(
        source_payloads={
            "ReviewDiff": "REVIEW CONTEXT: a representative unified diff body, long enough to matter."
        },
        label="dev/acceptance-sim",
        config=CONFIGURATION,
        run_fn=make_mock_run_agent(configuration=CONFIGURATION),
        base_dir=tmp_path,
    )

    report = grade(result.persist.session_dir)

    gate_fails = [c for c in report.checks if c.is_gate_fail]
    assert not gate_fails, format_table(report)
    assert not report.gate_failed

    # Name the high-value checks individually so a regression points at the offender.
    status = {(c.tier, c.check): c.status for c in report.checks}
    assert status[("T2", "output-integrity")] == PASS
    assert status[("T2", "judge-overlay")] == PASS
    assert status[("T2", "no-gate-failures")] == PASS
    assert status[("T1", "verdict-requires-judge")] == PASS
    assert status[("T1", "roster")] == PASS
    # Mock wires no ADO MCP → this adjudicates to NA (non-gating).
    assert status[("T2", "ado-mcp-connectivity")] == NA


def test_simulate_review_persists_counts(tmp_path: Path) -> None:
    """``run_review`` persists a ``counts`` block even when nothing blocks.

    The fix threads the publish-plan finding counts into ``trace.json``. The
    default mock yields only LOW findings and no resolvable overlay, so every
    count is 0 — but the block must be PRESENT (not ``None``), which is what lets
    the T1 critical-forces-reject check adjudicate instead of always returning NA.
    """
    result = run_review(
        source_payloads={
            "ReviewDiff": "REVIEW CONTEXT: a representative unified diff body, long enough to matter."
        },
        label="dev/acceptance-counts",
        config=CONFIGURATION,
        run_fn=make_mock_run_agent(configuration=CONFIGURATION),
        base_dir=tmp_path,
    )
    trace = json.loads(result.persist.trace_path.read_text(encoding="utf-8"))
    counts = trace.get("counts")
    assert counts is not None, "run_review must persist a counts block into the trace"
    assert counts["blocking"] == 0
    assert set(counts) == {"blocking", "nonBlocking", "all", "security"}


def _run_fn_with_blocking_overlay():
    """Mock ``run_fn`` that injects a Judge ``verdict_overlay`` flagging the
    default SchemaDrift finding as blocking, so the publish plan resolves one
    blocking finding through the REAL graph (everything else is the standard
    OVG-valid mock stub)."""
    base = make_mock_run_agent(configuration=CONFIGURATION)

    def run_fn(*, agent: str, **kw):
        if agent != "Judge":
            return base(agent=agent, **kw)
        submission = kw.pop("submission", None)
        result = base(agent=agent, submission=None, **kw)
        stub = build_valid_stub("Judge", configuration=CONFIGURATION)
        stub["verdict"] = "REJECT"
        # SchemaDrift's default stub (schema example) emits finding id ``SD-001``;
        # reference it so ``extract_publish_plan`` resolves a blocking finding.
        stub["verdict_overlay"] = [
            {
                "source_agent": "SchemaDrift",
                "finding_id": "SD-001",
                "blocking": True,
                "judge_justification": "Synthetic blocking ref for counts-teeth test.",
            }
        ]
        if submission is not None:
            submission.submit(stub)
        content = json.dumps(stub, separators=(",", ":"), ensure_ascii=False)
        return replace(result, final_content=content)

    return run_fn


def test_blocking_finding_gives_critical_check_teeth(tmp_path: Path) -> None:
    """A resolvable blocking finding makes T1 critical-forces-reject adjudicate.

    With the counts-threading fix, ``trace.counts.blocking`` reflects the publish
    plan, so the grader's critical-finding-forces-REJECT invariant has teeth on a
    review trace: blocking>0 with verdict REJECT must be PASS (not NA).
    """
    result = run_review(
        source_payloads={
            "ReviewDiff": "REVIEW CONTEXT: a representative unified diff body, long enough to matter."
        },
        label="dev/acceptance-teeth",
        config=CONFIGURATION,
        run_fn=_run_fn_with_blocking_overlay(),
        base_dir=tmp_path,
    )
    trace = json.loads(result.persist.trace_path.read_text(encoding="utf-8"))
    assert trace["counts"]["blocking"] == 1
    assert trace["verdict"] == "REJECT"

    checks = _grade_t1(trace, result.persist.session_dir)
    assert checks["critical-forces-reject"].status == PASS


def _grade_t1(trace: dict, session_dir: Path) -> dict:
    report = Report(session=str(session_dir))
    check_t1(
        report,
        session_dir,
        trace,
        observed_exit=None,
        configuration=CONFIGURATION,
    )
    return {c.check: c for c in report.checks}


def test_critical_forces_reject_pass(tmp_path: Path) -> None:
    """blocking>0 with verdict REJECT (overridden) satisfies the invariant."""
    trace = {
        "verdict": "REJECT",
        "verdictOverridden": True,
        "counts": {"blocking": 1},
        "agentCount": 1,
        "agents": [{"agent": "Judge", "response": "{}", "valid": True}],
    }
    checks = _grade_t1(trace, tmp_path)
    assert checks["critical-forces-reject"].status == PASS


def test_critical_forces_reject_fail(tmp_path: Path) -> None:
    """blocking>0 with a non-REJECT verdict is a T1 GATE failure."""
    trace = {
        "verdict": "APPROVE_WITH_SUGGESTIONS",
        "counts": {"blocking": 1},
        "agentCount": 1,
        "agents": [{"agent": "Judge", "response": "{}", "valid": True}],
    }
    report = Report(session=str(tmp_path))
    check_t1(
        report,
        tmp_path,
        trace,
        observed_exit=None,
        configuration=CONFIGURATION,
    )
    by = {c.check: c for c in report.checks}
    assert by["critical-forces-reject"].status == FAIL
    assert report.gate_failed  # a T1 FAIL rejects the run


def test_no_blocking_is_na(tmp_path: Path) -> None:
    """No blocking findings → the invariant has nothing to adjudicate (NA, non-gating)."""
    trace = {
        "verdict": "APPROVE",
        "counts": {"blocking": 0},
        "agentCount": 1,
        "agents": [{"agent": "Judge", "response": "{}", "valid": True}],
    }
    checks = _grade_t1(trace, tmp_path)
    assert checks["critical-forces-reject"].status == NA


def test_roster_requires_unconditional_agents(tmp_path: Path) -> None:
    """A bundle name is not a scheduling rule: missing A11y is a roster failure."""
    expected = roster(CONFIGURATION)
    agents = [
        {
            "agent": key,
            "response": "{}",
            "valid": True,
            "gate": None,
            "attempts": 1,
        }
        for key in expected
        if key != "Specialist_A11y"
    ]
    trace = {
        "verdict": "UNKNOWN",
        "agentCount": len(agents),
        "agents": agents,
    }

    checks = _grade_t1(trace, tmp_path)

    assert checks["roster"].status == FAIL
    assert checks["roster"].message == "missing required agents: ['Specialist_A11y']"


def test_roster_derives_inactive_agents_from_conditional_edges(tmp_path: Path) -> None:
    producer = GraphEntry("Producer", "", (), "P")
    conditional = GraphEntry(
        "Conditional",
        "",
        (Edge("Producer", when=Predicate("equals", field="run", value=True)),),
        "C",
    )
    configuration = Configuration._build_unchecked(
        name="conditional",
        root=tmp_path,
        entries=(producer, conditional),
        executor="dag",
        sink=None,
        max_steps=None,
    )
    trace = {
        "agents": [
            {
                "agent": "Producer",
                "response": '{"run": false}',
                "valid": True,
            }
        ]
    }

    assert roster(configuration, trace) == ["Producer"]

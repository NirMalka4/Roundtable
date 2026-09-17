"""Tests for the reserved engine-core Deviation/Feedback contract.

Contracts only — proves the shape and the projection from the existing OVG
producer (``PipelineResult.to_feedback``). No re-activation loop or budget guard
is built or exercised here.
"""

from __future__ import annotations

from roundtable.feedback import Deviation, Feedback
from roundtable.validation.pipeline import LeveledDiagnostic, PipelineResult


def test_deviation_blocking_by_level():
    assert Deviation("g", "error", "a.b", "m").blocking is True
    assert Deviation("g", "warn", "a.b", "m").blocking is False


def test_deviation_render_omits_empty_path():
    assert Deviation("json_schema", "error", "", "bad").render() == "[json_schema] bad"
    assert (
        Deviation("json_schema", "error", "findings[0].fix", "missing 'code'").render()
        == "[json_schema] findings[0].fix: missing 'code'"
    )


def test_feedback_blocking_and_filtering():
    fb = Feedback(
        target="Executor",
        deviations=(
            Deviation("g1", "warn", "", "advisory"),
            Deviation("g2", "error", "x", "blocks"),
        ),
    )
    assert fb.blocking is True
    assert fb.blocking_deviations == (Deviation("g2", "error", "x", "blocks"),)
    assert fb.render() == "[g1] advisory\n[g2] x: blocks"


def test_empty_feedback_is_non_blocking():
    fb = Feedback(target="n")
    assert fb.is_empty is True
    assert fb.blocking is False
    assert fb.render() == ""


def test_pipeline_result_projects_to_feedback():
    result = PipelineResult(
        passed=False,
        parsed=None,
        errors=[LeveledDiagnostic("json_schema", "error", "findings[0]", "missing code")],
        warnings=[LeveledDiagnostic("generic_phrase", "warn", "summary", "vague")],
    )
    fb = result.to_feedback("CodeCorrectness")
    assert fb.target == "CodeCorrectness"
    assert fb.blocking is True
    # errors precede warnings, in run order
    assert fb.deviations == (
        Deviation("json_schema", "error", "findings[0]", "missing code"),
        Deviation("generic_phrase", "warn", "summary", "vague"),
    )


def test_passing_result_projects_to_empty_feedback():
    result = PipelineResult(passed=True, parsed={}, errors=[], warnings=[])
    fb = result.to_feedback("n")
    assert fb.is_empty is True
    assert fb.blocking is False

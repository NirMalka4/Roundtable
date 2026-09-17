"""Unit tests for the session-report seam (``output.report``) and doctor Layer 14.

The seam exists so a config's ``verdict.md`` is rendered by code that knows THAT
config's Judge shape. These tests pin the three behaviours the seam must have — null
opts out, an unknown name fails loud, a duplicate registration is a wiring bug — plus
the InspectorX adapter's contract, which must keep behaving exactly as the inline code
it replaced.
"""

from __future__ import annotations

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.delivery.report import (
    ReportResult,
    get_report,
    register_report,
    report_names,
)
from roundtable.graph import get_configuration, register_config_plugins

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)


class _StubReport:
    name = "stub"

    def render(self, verdict, counts, session_results, **kwargs):
        return ReportResult(markdown="# stub\n")


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> dict:
    """A registry copy, so a test registration cannot leak into another test."""
    import roundtable.delivery.report as report_mod

    clone = dict(report_mod._REGISTRY)
    monkeypatch.setattr(report_mod, "_REGISTRY", clone)
    return clone


# ── get_report: null opts out, unknown fails loud ────────────────────────────
def test_none_resolves_to_no_renderer() -> None:
    """``report: null`` is a legitimate choice — that config gets the slim stub."""
    assert get_report(None) is None


def test_unknown_name_raises_naming_the_known_renderers() -> None:
    with pytest.raises(ValueError) as err:
        get_report("crystal-ball")
    assert "crystal-ball" in str(err.value)
    assert "known renderers" in str(err.value)


def test_registered_name_resolves_to_the_instance(isolated_registry: dict) -> None:
    stub = _StubReport()
    register_report("stub", stub)
    assert get_report("stub") is stub
    assert "stub" in report_names()


# ── register_report: a clash is a wiring bug, never a silent overwrite ────────
def test_duplicate_registration_raises(isolated_registry: dict) -> None:
    register_report("stub", _StubReport())
    with pytest.raises(ValueError, match="already registered"):
        register_report("stub", _StubReport())


# ── the shipped InspectorX renderer ──────────────────────────────────────────
def test_inspectorx_declares_and_resolves_its_renderer() -> None:
    assert _CONFIG.report == "verdict_overlay"
    assert get_report(_CONFIG.report) is not None


def test_verdict_overlay_returns_none_without_a_plan() -> None:
    """No publish plan ⇒ no report; the caller falls back to the slim verdict stub."""
    from roundtable.configs.inspectorx.plugins.report_renderer import VerdictOverlayReport
    from roundtable.decision.verdict import VerdictResult

    verdict = VerdictResult(
        verdict="APPROVE",
        verdict_icon="+",
        verdict_overridden=False,
        reason="",
        session_id="s1",
    )
    rendered = VerdictOverlayReport().render(
        verdict, None, {}, repo_name=None, source_branch=None, session_id="s1"
    )
    assert rendered is None


# ── doctor Layer 14: declared-but-unknown rejected, absent tolerated ─────────
def _draft(report: str | None):
    from roundtable.graph.model import Configuration

    base = _CONFIG
    return Configuration._build_unchecked(
        name="draft",
        root=base.root,
        entries=base.entries,
        executor=base.executor,
        sink=base.sink,
        projector=base.projector,
        report=report,
        max_steps=base.max_steps,
        domain_values=base.domain_values,
    )


def test_doctor_layer14_rejects_unknown_report() -> None:
    from roundtable.runtime.agent_setup import validate_agents

    report = validate_agents(config=_draft("crystal-ball"))
    assert any("session-report:" in e for e in report.errors)


def test_doctor_layer14_tolerates_absent_report() -> None:
    from roundtable.runtime.agent_setup import validate_agents

    report = validate_agents(config=_draft(None))
    assert not any("session-report:" in e for e in report.errors)

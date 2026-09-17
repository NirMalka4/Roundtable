"""The pluggable publish-projector seam (``output.projector``) + its config wiring.

Pins the topology-agnostic contract introduced by ``publish-projector-seam``: the
post-graph adapter that maps a session's per-agent results onto the neutral
``PublishableResult`` is registry-selected via a config's top-level ``projector:`` key
(mirroring the ``sink:`` / ``executor:`` seams), so a new graph topology can publish
by registering its own projector without editing ``ado``.

* :func:`get_projector` resolves a registered name lazily and rejects an unknown one
  — and ``None`` (no projector configured) — loudly, naming the known projectors.
  There is deliberately **no default**: projection is topology-specific.
* :func:`load_projector_name` defaults to ``None`` when the key is absent (no generic
  Roundtable default leaks into the loader).
* the Roundtable projector projects ``extract_publish_plan`` onto the neutral
  ``PublishableResult`` — its Roundtable-specific count-parity gate becomes
  ``abort_reason``, its operational log becomes ``log_summary``, and its finding
  counts become ``counts`` — so the generic sink never reads a ``PublishPlan`` field.
* doctor **Layer 13** rejects a config whose declared ``projector:`` names no
  registered adapter, and tolerates an absent (``None``) projector.
"""

from __future__ import annotations

import json

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.configs.inspectorx.plugins.verdict_overlay import (
    check_count_parity,
    extract_publish_plan,
)
from roundtable.delivery.projector import Projector, get_projector
from roundtable.graph import get_configuration, register_config_plugins

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)


# ── get_projector: resolve + reject ──────────────────────────────────────────
def test_get_projector_resolves_roundtable() -> None:
    projector = get_projector("verdict_overlay")
    assert isinstance(projector, Projector)  # runtime_checkable structural check
    assert projector.name == "verdict_overlay"


def test_get_projector_none_raises_naming_known() -> None:
    with pytest.raises(ValueError) as excinfo:
        get_projector(None)
    msg = str(excinfo.value)
    assert "verdict_overlay" in msg  # error names the known projectors


def test_get_projector_unknown_name_raises_naming_known() -> None:
    with pytest.raises(ValueError) as excinfo:
        get_projector("crystal-ball")
    msg = str(excinfo.value)
    assert "crystal-ball" in msg
    assert "verdict_overlay" in msg


# ── load_projector_name: default None, explicit read ─────────────────────────
def test_load_projector_name_defaults_none_when_key_absent(tmp_path) -> None:
    from roundtable.graph.loader import load_projector_name

    cfg = tmp_path / "no_projector.yaml"
    cfg.write_text("agents: []\n", encoding="utf-8")
    assert load_projector_name(cfg) is None


def test_load_projector_name_reads_explicit_key(tmp_path) -> None:
    from roundtable.graph.loader import load_projector_name

    cfg = tmp_path / "explicit_projector.yaml"
    cfg.write_text("projector: verdict_overlay\nagents: []\n", encoding="utf-8")
    assert load_projector_name(cfg) == "verdict_overlay"


# ── Roundtable projector projects extract_publish_plan onto the neutral result ─
def test_roundtable_projector_empty_result_is_none() -> None:
    session = {"Analyst": {"response": "{}"}}
    projector = get_projector("verdict_overlay")
    assert projector.project(session) is None
    assert extract_publish_plan(session) is None


def test_roundtable_projector_projects_plan_onto_neutral_result() -> None:
    specialist = {
        "response": json.dumps({"findings": [{"id": "A-1", "title": "t", "severity": "low"}]})
    }
    judge = {
        "response": json.dumps(
            {
                "verdict": "REJECT",
                "verdict_overlay": [
                    {
                        "source_agent": "Analyst",
                        "finding_id": "A-1",
                        "blocking": True,
                        "verdict_severity": "critical",
                    }
                ],
            }
        )
    }
    session = {"Analyst": specialist, "Judge": judge}
    result = get_projector("verdict_overlay").project(session)
    plan = extract_publish_plan(session)
    assert result is not None and plan is not None

    # The neutral fields the generic flow reads mirror the internal plan …
    assert result.verdict == plan.verdict == "REJECT"
    assert result.session_id == plan.session_id
    assert result.all_findings == plan.all_findings
    assert result.all_findings[0].severity == "Critical"
    # … the Roundtable count vocabulary is exposed generically …
    assert result.counts == {
        "blocking": len(plan.blocking_findings),
        "nonBlocking": len(plan.non_blocking_findings),
        "all": len(plan.all_findings),
        "security": len(plan.security_findings),
    }
    # … the count-parity gate becomes abort_reason (here: clean ⇒ None) …
    assert result.abort_reason == check_count_parity(plan)
    # … and the operational log line is carried for the sink to print.
    assert result.log_summary and "verdict=REJECT" in result.log_summary


def test_roundtable_projector_surfaces_parity_abort() -> None:
    """A count-parity violation is surfaced as ``abort_reason`` (not raised)."""
    # An unresolved overlay ref (no matching specialist finding) trips parity.
    judge = {
        "response": json.dumps(
            {
                "verdict": "REJECT",
                "verdict_overlay": [
                    {"source_agent": "Ghost", "finding_id": "missing", "blocking": True}
                ],
            }
        )
    }
    session = {"Judge": judge}
    result = get_projector("verdict_overlay").project(session)
    plan = extract_publish_plan(session)
    assert result is not None and plan is not None
    assert result.abort_reason is not None
    assert result.abort_reason == check_count_parity(plan)


# ── Configuration exposes the projector name ──────────────────────────────────
def test_configuration_exposes_projector() -> None:
    assert _CONFIG.projector == "verdict_overlay"


# ── doctor Layer 13: declared-but-unknown projector rejected; absent tolerated ─
def test_doctor_layer13_rejects_unknown_projector() -> None:
    from roundtable.graph.model import Configuration
    from roundtable.runtime.agent_setup import validate_agents

    base = _CONFIG
    draft = Configuration._build_unchecked(
        name="draft",
        root=base.root,
        entries=base.entries,
        executor=base.executor,
        sink=base.sink,
        projector="crystal-ball",
        max_steps=base.max_steps,
        domain_values=base.domain_values,
    )
    report = validate_agents(config=draft)
    assert any("publish-projector:" in e for e in report.errors)


def test_doctor_layer13_tolerates_absent_projector() -> None:
    from roundtable.graph.model import Configuration
    from roundtable.runtime.agent_setup import validate_agents

    base = _CONFIG
    draft = Configuration._build_unchecked(
        name="draft",
        root=base.root,
        entries=base.entries,
        executor=base.executor,
        sink=base.sink,
        projector=None,
        max_steps=base.max_steps,
        domain_values=base.domain_values,
    )
    report = validate_agents(config=draft)
    assert not any("publish-projector:" in e for e in report.errors)

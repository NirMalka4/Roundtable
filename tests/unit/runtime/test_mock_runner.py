"""``--simulate`` mock: the stub must be a genuinely OVG-valid stand-in.

The mock emits each agent's schema ``examples[0]`` (the structural SSOT). The one part a
repo-agnostic example cannot embed is the ``grounded_locations`` context gate — it needs
the review's diff. So the stub grounds every finding location's ``filePath`` against the
actual ``changed_files``. These tests pin that the stub passes the REAL runtime OVG
pipeline for the agents that escalate location grounding to ``level: error`` (which is why
they previously failed under ``--simulate``), and that grounding is a faithful, generic
transform (no per-agent field knowledge).
"""

from __future__ import annotations

import json
from functools import partial

from roundtable.backend.mock_runner import (
    build_valid_stub as _build_valid_stub,
)
from roundtable.backend.mock_runner import (
    make_mock_run_agent as _make_mock_run_agent,
)
from roundtable.bundle import resolve_bundle
from roundtable.graph.model import get_configuration
from roundtable.validation.pipeline import (
    evaluate_agent_output as _evaluate_agent_output,
)
from roundtable.validation.pipeline import (
    evaluate_agent_value as _evaluate_agent_value,
)

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
build_valid_stub = partial(_build_valid_stub, configuration=_CONFIG)
make_mock_run_agent = partial(_make_mock_run_agent, configuration=_CONFIG)
evaluate_agent_output = partial(_evaluate_agent_output, configuration=_CONFIG)
evaluate_agent_value = partial(_evaluate_agent_value, configuration=_CONFIG)

# A path that will never appear in a real diff, used as the grounding target so the gate's
# segment-match resolves it exactly.
_CHANGED = ["packages/app/src/PaymentService.ts"]


def _location_error_agents() -> list[str]:
    """Agents whose *ungrounded* stub is rejected by ``grounded_locations`` — i.e. the ones
    that escalate the location context gate to ``error``. Derived from the live graph so a
    roster change needs no edit here."""
    out: list[str] = []
    for entry in _CONFIG.entries:
        if not (entry.is_llm and entry.output_schema):
            continue
        raw = json.dumps(build_valid_stub(entry.key, changed_files=None))
        result = evaluate_agent_output(entry.key, raw, {"changed_files": _CHANGED})
        if not result.passed and "grounded_locations" in result.failed_gates:
            out.append(entry.key)
    return out


def test_the_failure_mode_exists() -> None:
    """Guard the guard: there IS at least one agent whose ungrounded stub fails grounding
    (else the grounding transform below would be vacuously green)."""
    assert _location_error_agents()


def test_grounded_stub_passes_ovg_for_all_location_error_agents() -> None:
    """With ``changed_files`` supplied, every location-error agent's stub passes the real
    OVG pipeline — the core of the ``--simulate`` fix."""
    for key in _location_error_agents():
        raw = json.dumps(build_valid_stub(key, changed_files=_CHANGED))
        result = evaluate_agent_output(key, raw, {"changed_files": _CHANGED})
        assert result.passed, (key, result.failed_gates, [e.message for e in result.errors])


def test_grounding_repoints_filepaths_to_a_changed_file() -> None:
    """Grounding rewrites finding location ``filePath`` values to a real changed file."""
    key = _location_error_agents()[0]
    stub = build_valid_stub(key, changed_files=_CHANGED)
    paths = [
        loc.get("filePath") for f in stub.get("findings", []) for loc in (f.get("locations") or [])
    ]
    assert paths and all(p == _CHANGED[0] for p in paths)


def test_grounding_is_noop_without_changed_files() -> None:
    """No diff → no grounding (matches the gate, which no-ops without ``changed_files``);
    the canned example path is preserved verbatim."""
    key = _location_error_agents()[0]
    plain = build_valid_stub(key, changed_files=None)
    default = build_valid_stub(key)
    assert plain == default
    paths = [
        loc.get("filePath") for f in plain.get("findings", []) for loc in (f.get("locations") or [])
    ]
    assert paths and all(p != _CHANGED[0] for p in paths)


def test_grounding_covers_replicated_multi_findings() -> None:
    """A multi-finding stub grounds every replicated finding's locations, not just the
    first — so ``findings_per_agent > 1`` stays fully OVG-valid."""
    key = _location_error_agents()[0]
    stub = build_valid_stub(key, findings_per_agent=3, changed_files=_CHANGED)
    findings = stub.get("findings", [])
    assert len(findings) == 3
    for f in findings:
        for loc in f.get("locations") or []:
            assert loc.get("filePath") == _CHANGED[0]


def test_make_mock_run_agent_threads_changed_files() -> None:
    """The ``run_fn`` factory forwards ``changed_files`` into the stub it emits."""
    key = _location_error_agents()[0]
    run_fn = make_mock_run_agent(changed_files=_CHANGED)
    result = run_fn(agent=key)
    parsed = json.loads(result.final_content)
    paths = [
        loc.get("filePath")
        for f in parsed.get("findings", [])
        for loc in (f.get("locations") or [])
    ]
    assert paths and all(p == _CHANGED[0] for p in paths)


def test_buddies_judge_stub_covers_runtime_finding_ids() -> None:
    configuration = get_configuration(resolve_bundle("buddies"))
    context = {
        "changed_files": _CHANGED,
        "upstream_finding_ids": [
            "bigoh::BH-01",
            "countercase::CC-01",
            "north_star::NS-01",
            "redgreen::RG-01",
            "smellcheck::SC-01",
            "taintcheck::TC-01",
        ],
    }

    stub = build_valid_stub(
        "Judge",
        configuration=configuration,
        validation_context=context,
    )
    result = evaluate_agent_value(
        "Judge",
        stub,
        context,
        configuration=configuration,
    )

    assert result.passed, (result.failed_gates, [error.message for error in result.errors])
    assert stub["verdict"]["summary"]
    assert {
        finding_id for claim in stub["claims"] for finding_id in claim["source_finding_ids"]
    } == set(context["upstream_finding_ids"])
    assert all(
        claim["primary_source_finding_id"] in claim["source_finding_ids"]
        for claim in stub["claims"]
    )

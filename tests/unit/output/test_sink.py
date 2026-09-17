"""The pluggable output-sink seam (``output.sink``) + its config wiring.

Pins the destination-agnostic sink contract so ADO
becomes one-of-many behind a registry-selected ``sink:`` key (mirroring the
``executor:`` seam):

* :func:`get_sink` resolves a registered name lazily and rejects an unknown one
  loudly (a config typo fails fast, naming the known sinks);
* :func:`load_sink_name` defaults to ``azure_devops`` when the key is absent, and
  preserves an explicit ``sink: null`` (a config that declares it does not publish);
* the concrete ``azure_devops`` adapter satisfies the :class:`Sink` protocol and
  maps a successful publish/unpublish onto a clean :class:`SinkOutcome`;
* doctor **Layer 12** rejects a config whose ``sink:`` names no registered adapter.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.decision.verdict import EXIT_CLEAN
from roundtable.delivery.sink import Sink, SinkOutcome, get_sink
from roundtable.graph import get_configuration, register_config_plugins

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
register_config_plugins(_CONFIG)


# ── get_sink: resolve + reject ───────────────────────────────────────────────
def test_get_sink_resolves_azure_devops() -> None:
    sink = get_sink("azure_devops")
    assert isinstance(sink, Sink)  # runtime_checkable structural check
    assert sink.name == "azure_devops"


def test_get_sink_default_is_azure_devops() -> None:
    assert get_sink().name == "azure_devops"


def test_get_sink_unknown_name_raises_naming_known() -> None:
    with pytest.raises(ValueError) as excinfo:
        get_sink("carrier-pigeon")
    msg = str(excinfo.value)
    assert "carrier-pigeon" in msg
    assert "azure_devops" in msg  # error names the known sinks


# ── SinkOutcome shape ────────────────────────────────────────────────────────
def test_sink_outcome_defaults_message_empty() -> None:
    outcome = SinkOutcome(ok=True, exit_code=EXIT_CLEAN)
    assert (outcome.ok, outcome.exit_code, outcome.message) == (True, EXIT_CLEAN, "")


# ── load_sink_name: default when the key is absent ───────────────────────────
def test_load_sink_name_defaults_when_key_absent(tmp_path) -> None:
    from roundtable.graph.loader import load_sink_name

    cfg = tmp_path / "no_sink.yaml"
    cfg.write_text("agents: []\n", encoding="utf-8")
    assert load_sink_name(cfg) == "azure_devops"


def test_load_sink_name_reads_explicit_key(tmp_path) -> None:
    from roundtable.graph.loader import load_sink_name

    cfg = tmp_path / "explicit_sink.yaml"
    cfg.write_text("sink: azure_devops\nagents: []\n", encoding="utf-8")
    assert load_sink_name(cfg) == "azure_devops"


# ── `sink: null` — an OPTED-OUT config, not a defaulted one ──────────────────
def test_explicit_null_sink_is_not_folded_into_the_default(tmp_path) -> None:
    """``sink: null`` must survive the loader.

    ``config_meta.schema.yaml`` has always accepted null here, so a config could
    declare "I do not publish" and be silently handed the ADO sink anyway — the
    declaration was a no-op. Absent still defaults; explicit null does not.
    """
    from roundtable.graph.loader import load_sink_name

    cfg = tmp_path / "null_sink.yaml"
    cfg.write_text("sink: null\nagents: []\n", encoding="utf-8")
    assert load_sink_name(cfg) is None


def test_get_sink_none_resolves_to_no_sink_rather_than_raising() -> None:
    """Publishing is optional and config-owned; declaring none is not an error."""
    assert get_sink(None) is None


def test_publish_on_a_config_with_no_sink_reports_instead_of_crashing(
    monkeypatch, capsys, tmp_path
) -> None:
    from roundtable import cli
    from roundtable.decision.verdict import EXIT_BAD_ARGS

    monkeypatch.setattr(
        "roundtable.graph.get_configuration",
        lambda *_a, **_k: SimpleNamespace(sink=None),
    )
    monkeypatch.setattr("roundtable.bundle.config_root", lambda: _CONFIG.root)
    (tmp_path / "graph.json").write_text('{"displayName":"inspectorx"}', encoding="utf-8")
    code = cli._run_publish_for_session(str(tmp_path), SimpleNamespace())
    assert code == EXIT_BAD_ARGS
    assert "no output sink" in capsys.readouterr().err


def test_unpublish_on_a_config_with_no_sink_reports_instead_of_crashing(
    monkeypatch, capsys, tmp_path
) -> None:
    from roundtable import cli
    from roundtable.decision.verdict import EXIT_BAD_ARGS

    monkeypatch.setattr(
        "roundtable.graph.get_configuration",
        lambda *_a, **_k: SimpleNamespace(sink=None),
    )
    monkeypatch.setattr("roundtable.bundle.config_root", lambda: _CONFIG.root)
    (tmp_path / "graph.json").write_text('{"displayName":"inspectorx"}', encoding="utf-8")
    args = SimpleNamespace(session=str(tmp_path), dry_run=True, pr=None, out=None)
    code = cli._cmd_unpublish(args)
    assert code == EXIT_BAD_ARGS
    assert "no output sink" in capsys.readouterr().err


# ── AzureDevOpsSink: protocol conformance + clean-outcome smoke ───────────────
def test_azure_devops_sink_publish_maps_ok_report_to_clean(monkeypatch, capsys) -> None:
    from roundtable.ado import sink as ado_sink
    from roundtable.ado.publish_flow import PublishOptions

    plan = SimpleNamespace(
        all_findings=[],
        session_id="s1",
        verdict="APPROVE",
        counts={"blocking": 0, "nonBlocking": 0, "all": 0, "security": 0},
        abort_reason=None,
        log_summary="plan: verdict=APPROVE ✅ | blocking=0 non-blocking=0 security=0 safe=0 | observations=0",
    )
    report = SimpleNamespace(
        anchor_warnings=[],
        total=0,
        eligible=0,
        skipped_by_threshold=0,
        inline=0,
        general=0,
        dry_run_path=None,
        results=[],
        summary_result=SimpleNamespace(
            finding_id="__summary__", status="skipped", detail="summary already current"
        ),
        already_published=False,
        posted=0,
        skipped=0,
        failed=0,
        ok=True,
        version_label=None,
        label_action="skipped",
    )
    monkeypatch.setattr(ado_sink, "_session_results_from_trace", lambda _d: {"Judge": {}})
    monkeypatch.setattr(
        ado_sink, "get_projector", lambda _name: SimpleNamespace(project=lambda *a, **k: plan)
    )
    monkeypatch.setattr(ado_sink, "_read_subject", lambda _d: (None, None))
    monkeypatch.setattr(ado_sink, "run_publish", lambda *a, **k: report)

    outcome = ado_sink.AzureDevOpsSink().publish("/sess", PublishOptions())

    assert outcome == SinkOutcome(ok=True, exit_code=EXIT_CLEAN)
    assert "summary: skipped (summary already current)" in capsys.readouterr().err


# ── replay fidelity: publish reads the run's failure facts, not just responses ─
def _session_dir(tmp_path, *, trace: dict | None = None, raw: dict | None = None):
    import json

    if trace is not None:
        (tmp_path / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    if raw is not None:
        (tmp_path / "raw_results.json").write_text(json.dumps(raw), encoding="utf-8")
    return str(tmp_path)


_TIMED_OUT_AGENT = {
    "agent": "redgreen",
    "response": "",
    "valid": False,
    "gate": None,
    "attempts": 1,
    "attemptsDetail": [{"outcome": "api_error", "errors": [{"message": "timeout"}]}],
}


def test_replay_carries_the_failure_facts_beside_the_response(tmp_path) -> None:
    """``raw_results.json`` holds only responses, so an agent that timed out and one
    that said nothing arrive identical — on the very surface a human reads to judge
    whether the review was complete."""
    from roundtable.ado import sink as ado_sink

    session_dir = _session_dir(
        tmp_path, trace={"agents": [_TIMED_OUT_AGENT]}, raw={"redgreen": {"response": ""}}
    )
    results = ado_sink._session_results_from_trace(session_dir)
    assert results["redgreen"]["valid"] is False
    assert results["redgreen"]["attemptsDetail"][0]["outcome"] == "api_error"


def test_replay_prefers_the_normalized_response_over_the_trace(tmp_path) -> None:
    from roundtable.ado import sink as ado_sink

    session_dir = _session_dir(
        tmp_path,
        trace={"agents": [{"agent": "Judge", "response": "stale"}]},
        raw={"Judge": {"response": "fresh"}},
    )
    assert ado_sink._session_results_from_trace(session_dir)["Judge"]["response"] == "fresh"


def test_replay_still_blocks_loudly_when_the_trace_is_the_only_source(tmp_path) -> None:
    """Enrichment is tolerant; the trace being unreadable when it IS the source is not."""
    from roundtable.ado import sink as ado_sink

    with pytest.raises(OSError):
        ado_sink._session_results_from_trace(str(tmp_path))


# ── doctor Layer 12: unknown sink is rejected offline ────────────────────────
def test_doctor_layer12_rejects_unknown_sink() -> None:
    from roundtable.graph.model import Configuration
    from roundtable.runtime.agent_setup import validate_agents

    base = _CONFIG
    draft = Configuration._build_unchecked(
        name="draft",
        root=base.root,
        entries=base.entries,
        executor=base.executor,
        sink="carrier-pigeon",
        max_steps=base.max_steps,
        domain_values=base.domain_values,
    )
    report = validate_agents(config=draft)
    assert any("output-sink:" in e for e in report.errors)

"""Consolidated static validation (`validate_agents`) + the `doctor` CLI.

These gate the SSOT against the prompt bundle: graph integrity + every is_llm
entry's instructions + shared_context files exist and it declares model +
only registry-known mcp_servers (all in agent_graph.yaml).

Defect-injection is done by building a **draft Configuration** with the defect and
passing it via ``validate_agents(config=...)`` — dogfooding the config seam rather
than monkeypatching module globals (the doctor-as-validation-service contract:
validation works on ANY named config, and returns a ``ValidationReport``).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.capabilities import (
    RuntimeCapabilities,
    always_on_tool_names,
    builtin_tool_names,
)
from roundtable.cli import main as cli_main
from roundtable.graph.model import (
    Configuration,
    PublishingPolicy,
    get_configuration,
    register_config_plugins,
)
from roundtable.runtime.agent_setup import validate_agents

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
_PROMPT_ROOT = _CONFIG.root / "prompts" / "Reviewer"


@pytest.fixture(autouse=True)
def _register_bundle_plugins() -> None:
    register_config_plugins(_CONFIG)


def _capabilities(
    *,
    models=None,
    builtin=None,
    always_on=None,
) -> RuntimeCapabilities:
    configured_models = {entry.model for entry in _CONFIG.entries if entry.is_llm and entry.model}
    return RuntimeCapabilities(
        model_ids=frozenset(configured_models if models is None else models),
        builtin_tool_ids=frozenset(builtin_tool_names() if builtin is None else builtin),
        always_on_tool_ids=frozenset(always_on_tool_names() if always_on is None else always_on),
        cli_version="1.0.84-1",
        sdk_package_version="1.0.10rc1",
        sdk_protocol_version="3",
        tool_source="cache",
    )


def _config_from(entries, *, sink_cardinality=None) -> Configuration:
    """A draft Configuration identical to the shipped one but for ``entries``."""
    base = _CONFIG
    return Configuration._build_unchecked(
        name="draft",
        root=base.root,
        entries=tuple(entries),
        executor=base.executor,
        sink=base.sink,
        max_steps=base.max_steps,
        sink_cardinality=sink_cardinality or base.sink_cardinality,
        domain_values=base.domain_values,
        publishing=base.publishing,
    )


def _replace_one(key, **overrides):
    """The real graph's entries with a single entry's fields overridden — isolating
    the injected defect so every other layer keeps validating a pristine graph."""
    return tuple(replace(e, **overrides) if e.key == key else e for e in _CONFIG.entries)


def test_validate_agents_passes_on_real_bundle():
    # The vendored bundle satisfies every is_llm entry → a clean, non-raising report.
    report = validate_agents(_PROMPT_ROOT, config=_CONFIG)
    assert report.ok
    assert report.errors == []


def test_validate_agents_returns_report_not_raising_on_defect():
    """The service returns a ``ValidationReport`` (never raises for validation errors);
    the fail-loud contract is opt-in via ``raise_if_failed``."""
    cfg = _config_from(_replace_one("Judge", model=None))
    report = validate_agents(config=cfg)  # does NOT raise
    assert not report.ok
    with pytest.raises(ValueError):
        report.raise_if_failed()


def test_validate_agents_flags_missing_prompt_files(tmp_path):
    # An empty bundle root → every is_llm entry's prompt file is absent.
    with pytest.raises(ValueError) as exc:
        validate_agents(tmp_path, config=_CONFIG).raise_if_failed()
    msg = str(exc.value)
    assert "validate_agents failed" in msg
    assert "instructions file not found" in msg
    # A representative is_llm agent is reported.
    assert "Judge" in msg


def test_validate_agents_flags_missing_shared_context():
    cfg = _config_from(_replace_one("Judge", shared_context=("Shared/DoesNotExist.md",)))
    report = validate_agents(config=cfg)
    msg = "\n".join(report.errors)
    assert "shared_context file not found" in msg
    assert "Shared/DoesNotExist.md" in msg


def test_validate_agents_flags_missing_model():
    cfg = _config_from(_replace_one("Judge", model=None))
    report = validate_agents(config=cfg)
    assert any("declares no `model`" in e for e in report.errors)


def test_validate_agents_accepts_explicit_live_model_availability():
    report = validate_agents(config=_CONFIG, runtime_capabilities=_capabilities())
    assert report.ok


def test_validate_agents_groups_unavailable_models_into_one_failure():
    entries = tuple(
        replace(entry, model="not-entitled") if entry.key in {"Deadlock", "Judge"} else entry
        for entry in _CONFIG.entries
    )
    account_models = {
        entry.model
        for entry in _CONFIG.entries
        if entry.is_llm and entry.model and entry.key not in {"Deadlock", "Judge"}
    }
    report = validate_agents(
        config=_config_from(entries),
        runtime_capabilities=_capabilities(models=account_models),
    )

    assert not report.ok
    assert len(report.errors) == 1


def test_validate_agents_treats_an_empty_live_model_list_as_unavailable():
    report = validate_agents(config=_CONFIG, runtime_capabilities=_capabilities(models=set()))
    assert not report.ok
    assert len(report.errors) == 1


def test_validate_agents_rejects_unknown_publishing_floor():
    cfg = replace(
        _CONFIG,
        publishing=PublishingPolicy(default_min_severity="urgent"),
    )
    report = validate_agents(config=cfg)
    assert any(
        "publishing.default_min_severity 'urgent' is not in domain_values.severity" in error
        for error in report.errors
    )


def test_graph_validation_requires_severity_values_for_publishing_floor(tmp_path):
    from roundtable.graph import validate_graph_config

    cfg = Configuration.from_document(
        {
            "name": "no-severity",
            "publishing": {"default_min_severity": "medium"},
            "agents": [{"key": "Source", "kind": "source", "emoji": "S"}],
        },
        root=tmp_path,
    )
    with pytest.raises(
        ValueError,
        match=r"publishing\.default_min_severity 'medium' requires domain_values\.severity",
    ):
        validate_graph_config(cfg.entries, cfg)


def test_validate_agents_flags_dependency_cycle():
    """Acyclicity now reaches doctor via the selected executor's validate (Layer 1b),
    not the executor-agnostic validate_graph_config. Inject a 2-cycle into the REAL
    graph (adding edges only) so every other layer stays green and the cycle is the
    reported violation."""
    import roundtable.graph.model as gc

    real = _CONFIG.entries
    a, b = real[0].key, real[1].key

    def _with_edge(e, dep):
        return replace(e, edges=(*e.edges, gc.Edge(dep)))

    patched = list(real)
    patched[0] = _with_edge(patched[0], b)  # real[0] now depends on real[1] …
    patched[1] = _with_edge(patched[1], a)  # … and real[1] on real[0] → a cycle
    report = validate_agents(config=_config_from(patched))
    assert any("dependency cycle" in e for e in report.errors)


def test_validate_agents_flags_unknown_mcp_server():
    from roundtable.graph.model import McpBinding

    cfg = _config_from(_replace_one("Judge", mcp=(McpBinding(server="zz_not_a_real_server"),)))
    report = validate_agents(config=cfg)
    msg = "\n".join(report.errors)
    assert "unknown MCP server" in msg
    assert "zz_not_a_real_server" in msg


def test_validate_agents_flags_duplicate_tools():
    cfg = _config_from(_replace_one("Judge", tools=("view", "view")))
    report = validate_agents(config=cfg)
    assert any("duplicate entries in `tools`" in error for error in report.errors)


def test_validate_agents_flags_unknown_builtin_tool():
    cfg = _config_from(_replace_one("Judge", tools=("browse",)))
    report = validate_agents(config=cfg)
    assert not report.ok
    assert len(report.errors) == 1


def test_live_runtime_rejects_removed_tool_before_review() -> None:
    cfg = _config_from(_replace_one("Judge", tools=("view",)))
    report = validate_agents(
        config=cfg,
        runtime_capabilities=_capabilities(builtin=builtin_tool_names() - {"view"}),
    )

    assert not report.ok
    assert len(report.errors) == 1


def test_live_runtime_accepts_new_tool_absent_from_packaged_baseline() -> None:
    tool = "new_runtime_tool"
    assert tool not in builtin_tool_names()
    cfg = _config_from(_replace_one("Judge", tools=(tool,)))

    report = validate_agents(
        config=cfg,
        runtime_capabilities=_capabilities(builtin=builtin_tool_names() | {tool}),
    )

    assert report.ok


def test_validate_agents_rejects_policy_for_ungranted_tool():
    from roundtable.graph import PowershellToolPolicy, ToolPolicy

    policy = ToolPolicy(powershell=PowershellToolPolicy(120, False))
    cfg = _config_from(_replace_one("Judge", tools=("view",), tool_policy=policy))
    report = validate_agents(config=cfg)
    assert any(
        "tool_policy configures `powershell` but `tools` does not grant it" in error
        for error in report.errors
    )


def test_validate_agents_flags_dead_alias_as_unknown_tool():
    # `search` is published as a built-in alias but expands to nothing in this
    # runtime, which is how reviewers silently ran without grep/glob.
    cfg = _config_from(_replace_one("Judge", tools=("search",)))
    report = validate_agents(config=cfg)
    assert not report.ok
    assert len(report.errors) == 1


def test_validate_agents_flags_always_on_tool_declaration():
    cfg = _config_from(_replace_one("Judge", tools=("sql",)))
    report = validate_agents(config=cfg)
    assert not report.ok
    assert len(report.errors) == 1


def test_validate_agents_requires_mcp_tool_server_on_same_agent():
    cfg = _config_from(_replace_one("Judge", tools=("ado-work-items/wit_get_work_item",), mcp=()))
    report = validate_agents(config=cfg)
    assert any("is not declared by this agent" in error for error in report.errors)


def test_validate_agents_flags_unknown_tool_in_server_inventory():
    from roundtable.graph.model import McpBinding

    cfg = _config_from(
        _replace_one(
            "Judge",
            tools=("ado-work-items/not_a_tool",),
            mcp=(McpBinding(server="ado-work-items"),),
        )
    )
    report = validate_agents(config=cfg)
    assert any("is absent from MCP server" in error for error in report.errors)


def test_validate_agents_flags_dangling_cross_agent_ref():
    """Layer 6 is wired: dropping the SecurityFocusPack edge from a consumer whose
    prompt still references ``security_focus_pack.<field>`` fails the doctor."""
    consumer = next(e for e in _CONFIG.entries if e.key == "AttackSurfaceScanner")
    kept = tuple(ed for ed in consumer.edges if ed.source != "SecurityFocusPack")
    cfg = _config_from(_replace_one("AttackSurfaceScanner", edges=kept))
    report = validate_agents(config=cfg)
    msg = "\n".join(report.errors)
    assert "security_focus_pack" in msg
    assert "AttackSurfaceScanner" in msg


def test_validate_agents_flags_multiple_runnable_sinks():
    """Layer 11: the output-contract requires exactly one runnable sink. Dropping the
    ``Verdict`` node's soft Judge edge leaves Judge unconsumed too, so both Judge and
    Verdict become out-degree-0 runnable sinks — an ambiguous domain-result read the
    doctor must reject. Threading the draft config to Layer 11 (via
    ``_runnable_graph_entries(cfg)``) surfaces the defect with no monkeypatching."""
    cfg = _config_from(_replace_one("Verdict", edges=()))
    report = validate_agents(config=cfg)
    msg = "\n".join(report.errors)
    assert "expected exactly one runnable graph sink" in msg
    assert "Judge" in msg and "Verdict" in msg


def test_sink_cardinality_any_opts_out_of_the_single_sink_check():
    """Layer 11 is config-declared, not a hardcoded law. The SAME multi-sink defect
    that ``one`` rejects is *accepted* when the config declares
    ``sink_cardinality: any`` — proving the check is driven by the config contract,
    not by a fixed opinion baked into the doctor."""
    entries = _replace_one("Verdict", edges=())
    rejected = validate_agents(config=_config_from(entries, sink_cardinality="one"))
    accepted = validate_agents(config=_config_from(entries, sink_cardinality="any"))

    assert any("runnable graph sink" in e for e in rejected.errors)
    assert not any("runnable graph sink" in e for e in accepted.errors)


def test_doctor_cli_exit_zero():
    assert cli_main(["doctor", "--static"]) == 0


def test_agent_identity_passes_on_real_graph():
    """Layer 4 (identity) is clean for the shipped graph."""
    from roundtable.runtime.agent_setup import _validate_agent_identity

    assert _validate_agent_identity(_CONFIG.entries) == []


def test_agent_identity_flags_duplicate_canonical_id():
    from roundtable.runtime.agent_setup import _validate_agent_identity

    # Point Deadlock's agent_id at Judge's canonical id ⇒ two entries collide.
    broken = tuple(
        replace(e, agent_id="judge") if e.key == "Deadlock" else e for e in _CONFIG.entries
    )
    errs = _validate_agent_identity(broken)
    assert any("Deadlock" in m and "duplicate canonical id" in m for m in errs)


def test_agent_identity_flags_missing_agent_id():
    """A non-source entry with no agent_id is rejected — its OVG/finding-index
    lookups would silently miss (the fail-open this guard nets)."""
    from roundtable.runtime.agent_setup import _validate_agent_identity

    broken = tuple(replace(e, agent_id=None) if e.key == "Deadlock" else e for e in _CONFIG.entries)
    errs = _validate_agent_identity(broken)
    assert any("Deadlock" in m and "missing agent_id" in m for m in errs)


def test_agent_identity_flags_missing_display_name():
    from roundtable.runtime.agent_setup import _validate_agent_identity

    broken = tuple(
        replace(e, display_name=None) if e.key == "Deadlock" else e for e in _CONFIG.entries
    )
    errs = _validate_agent_identity(broken)
    assert any("Deadlock" in m and "display_name" in m for m in errs)

"""Unit tests for the MCP-server registry + per-invocation config resolution.

Pins structured role-scoped ADO config, not-buildable → skip behaviour,
deterministic multi-server merge, and the registry metadata consumed by doctor.
"""

from __future__ import annotations

from roundtable.mcp import (
    McpBuildContext,
    mcp_server_placeholders,
    mcp_server_specs,
    registered_server_names,
    resolve_mcp_config,
    server_bindings,
    server_tool_names,
    validate_mcp_specs,
)
from roundtable.mcp import registry as reg


#: The canonical ``ado-work-items`` config the YAML registry must emit for org
#: "contoso". A literal snapshot is the regression guard now the ado spec lives
#: only in ``mcp_servers.yaml`` (no in-code builder to compare against). The
#: ``requires``/``bindings`` META fields are NOT emitted.
def test_resolve_ado_returns_structured_server_map():
    ctx = McpBuildContext(ado_org="contoso")
    cfg = resolve_mcp_config(["ado-work-items"], ctx)
    assert cfg is not None
    assert cfg["ado-work-items"]["command"] == "npx"
    assert cfg["ado-work-items"]["args"][2] == "contoso"


def test_resolve_strips_ado_org():
    cfg = resolve_mcp_config(["ado-work-items"], McpBuildContext(ado_org="  contoso  "))
    assert cfg["ado-work-items"]["args"][2] == "contoso"


def test_bindings_meta_not_emitted():
    cfg = resolve_mcp_config(["ado-work-items"], McpBuildContext(ado_org="o"))
    server = cfg["ado-work-items"]
    assert "bindings" not in server
    assert "requires" not in server


def test_resolve_returns_none_when_ado_org_absent():
    assert resolve_mcp_config(["ado-work-items"], McpBuildContext()) is None
    assert resolve_mcp_config(["ado-work-items"], McpBuildContext(ado_org="  ")) is None


def test_resolve_returns_none_for_empty_request():
    assert resolve_mcp_config([], McpBuildContext(ado_org="contoso")) is None


def test_resolve_skips_unknown_server_names():
    ctx = McpBuildContext(ado_org="contoso")
    cfg = resolve_mcp_config(["ado-work-items", "kusto-not-registered"], ctx)
    assert set(cfg) == {"ado-work-items"}


def test_registered_names_contain_both_roles():
    names = registered_server_names()
    assert "ado-work-items" in names
    assert "ado-publish" in names


def test_server_bindings_surface():
    wi = server_bindings("ado-work-items")
    assert ("Get pull request", "repo_get_pull_request_by_id") in wi
    assert ("Batch get work items", "wit_get_work_items_batch_by_ids") in wi
    # unknown server yields no rows
    assert server_bindings("nope") == ()


def test_server_tool_inventory_surface():
    assert "wit_get_work_item" in server_tool_names("ado-work-items")
    assert server_tool_names("nope") == frozenset()


def test_parse_bindings_rejects_tool_absent_from_allowlist():
    """A binding advertising a tool not in the `tools:` allowlist fails loud —
    else the ADO Tool-Bindings section would name a tool the server never exposes."""
    import pytest

    spec = {"tools": ["repo_get_pull_request_by_id"], "bindings": [["Ghost", "repo_ghost_tool"]]}
    with pytest.raises(ValueError, match="absent from its `tools:` allowlist"):
        reg._parse_bindings("ado-x", spec)


def test_parse_bindings_allows_any_tool_under_wildcard():
    """A `tools: ["*"]` wildcard server may bind any tool name."""
    spec = {"tools": ["*"], "bindings": [["Anything", "repo_anything"]]}
    assert reg._parse_bindings("srv", spec) == (("Anything", "repo_anything"),)


def test_ado_servers_use_noninteractive_azcli_auth():
    # Part (b): both ADO specs must pass `--authentication azcli` so the vendor
    # server reuses the ambient `az login` session instead of defaulting to the
    # `interactive` browser prompt a headless agent/prewarm can never satisfy.
    ctx = McpBuildContext(ado_org="contoso")
    for name in ("ado-work-items", "ado-publish"):
        cfg = resolve_mcp_config([name], ctx)
        args = cfg[name]["args"]
        assert "--authentication" in args, name
        assert args[args.index("--authentication") + 1] == "azcli", name


def test_yaml_requires_drops_server_without_context(monkeypatch):
    """A `requires:` field that is empty in context drops the server (None)."""
    builder = reg._make_builder({"command": "x", "args": ["${ado_org}"], "requires": ["ado_org"]})
    assert builder(McpBuildContext()) is None
    assert builder(McpBuildContext(ado_org="org"))["args"] == ["org"]


def test_multi_server_merge_is_sorted_and_deduped(monkeypatch):
    """A second registered server merges into one config with deterministic keys."""
    fake_registry = dict(reg._REGISTRY)
    fake_registry["zzz"] = lambda ctx: {"command": "z", "args": []}
    monkeypatch.setattr(reg, "_REGISTRY", fake_registry)

    ctx = McpBuildContext(ado_org="contoso")
    out = resolve_mcp_config(["zzz", "ado-work-items", "ado-work-items"], ctx)
    assert list(out) == ["ado-work-items", "zzz"]


def test_builder_returning_none_is_skipped(monkeypatch):
    fake_registry = dict(reg._REGISTRY)
    fake_registry["maybe"] = lambda ctx: None  # never buildable
    monkeypatch.setattr(reg, "_REGISTRY", fake_registry)
    assert resolve_mcp_config(["maybe"], McpBuildContext()) is None


# ── CLI tool-name mapping (what an agent's MCP grant is measured against) ────


def test_cli_tool_name_prefixes_with_the_servers_logical_name():
    """The registry header states it: logical name IS the CLI tool-name prefix."""
    assert reg.cli_tool_name("ado-work-items", "wit_get_work_item") == (
        "ado-work-items-wit_get_work_item"
    )


def test_the_inventory_covers_every_registered_servers_tools():
    inventory = reg.cli_tool_inventory()
    for name in registered_server_names():
        for tool in server_tool_names(name):
            assert reg.cli_tool_name(name, tool) in inventory
    assert len(inventory) == sum(len(server_tool_names(n)) for n in registered_server_names())


def test_an_unknown_server_contributes_nothing_to_the_inventory():
    assert not any(t.startswith("no-such-server-") for t in reg.cli_tool_inventory())


def test_registry_introspection_returns_detached_specs_and_placeholders():
    specs = mcp_server_specs()
    specs["ado-work-items"]["command"] = "changed"

    assert mcp_server_specs()["ado-work-items"]["command"] == "npx"
    assert mcp_server_placeholders("ado-work-items") == {"ado_org"}
    assert mcp_server_placeholders("unknown") == frozenset()


def test_mcp_spec_validation_reports_type_placeholder_and_binding_defects():
    document = {
        "servers": {
            "broken": {
                "command": 7,
                "args": ["${missing}", "${ado_org}"],
                "tools": ["allowed"],
                "requires": ["missing"],
                "timeout": "slow",
                "bindings": [["Advertised", "not-allowed"]],
            }
        }
    }

    assert validate_mcp_specs(document) == [
        "mcp_servers.yaml: server 'broken' `command` must be a non-empty string",
        "mcp_servers.yaml: server 'broken' `timeout` must be an integer",
        "mcp_servers.yaml: server 'broken' requires unknown McpBuildContext field 'missing' "
        "(known: ['ado_org'])",
        "mcp_servers.yaml: server 'broken' uses unknown McpBuildContext placeholder "
        "${missing} (known: ['ado_org'])",
        "mcp_servers.yaml: server 'broken' placeholder ${ado_org} must also appear in `requires`",
        "mcp_servers.yaml: server 'broken' binds capability 'Advertised' to tool 'not-allowed' "
        "which is absent from its `tools:` allowlist ['allowed']",
    ]


def test_mcp_spec_validation_accepts_static_and_context_bound_servers():
    assert (
        validate_mcp_specs(
            {
                "servers": {
                    "static": {"command": "python", "args": ["server.py"]},
                    "dynamic": {
                        "command": "npx",
                        "args": ["${ado_org}"],
                        "tools": ["read"],
                        "requires": ["ado_org"],
                        "bindings": [["Read", "read"]],
                    },
                }
            }
        )
        == []
    )


def test_doctor_validation_reuses_the_public_mcp_registry_seam(monkeypatch):
    import roundtable.mcp as mcp
    from roundtable.bundle import resolve_bundle
    from roundtable.graph import get_configuration, register_config_plugins
    from roundtable.runtime import validate_agents

    config = get_configuration(resolve_bundle("inspectorx"))
    register_config_plugins(config)
    monkeypatch.setattr(mcp, "validate_mcp_registry", lambda: ["registry defect"])

    prompt_root = config.root / "prompts" / "Reviewer"
    assert "registry defect" in validate_agents(prompt_root, config=config).errors

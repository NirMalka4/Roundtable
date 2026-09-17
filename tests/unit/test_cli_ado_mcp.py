"""Integration tests for the CLI ADO-MCP wiring.

Exercises ``_build_review_context`` in local mode with dependencies stubbed so the
ADO decision logic (identity → sections + structured MCP context) is isolated from live git /
network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from roundtable import cli
from roundtable.inputs.ado_identity import AdoIdentity
from roundtable.mcp.registry import resolve_mcp_config


@pytest.fixture
def _stub_local(monkeypatch):
    """Stub base-branch resolution + git-context gathering for local mode."""
    monkeypatch.setattr(cli, "_resolve_verify_base", lambda *a, **k: "origin/main")
    monkeypatch.setattr(
        cli,
        "gather_review_context",
        lambda opts: SimpleNamespace(
            git_context="=== Repository: R ===\n-- Diff --\ndiff --git a/x b/x\n",
            metadata=SimpleNamespace(snapshot_sha="deadbeef", base_sha="cafebabe"),
            changed_files=["x"],
        ),
    )


def _repo():
    return SimpleNamespace(
        name="ExampleRepo",
        branch="feat",
        remote_url="https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo",
    )


def _args(**kw):
    base = {"pr": None, "base_branch": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _workspace():
    """A local in-place review workspace (working tree at Path('.'))."""
    return SimpleNamespace(path=cli.Path("."), mode="live-checkout")


def _ident():
    return AdoIdentity(
        org="contoso",
        project="ExampleProject",
        repo_name="ExampleRepo",
        remote_url="https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo",
        host="dev.azure.com",
        repository_id="rid",
        project_id="pid",
    )


def test_local_ado_enabled_returns_identities_and_mcp_context(monkeypatch, _stub_local):
    monkeypatch.setattr(cli, "resolve_ado_identities", lambda **k: [_ident()])
    _, header, mcp_ctx, _inj, changed_files, identities, _subject = cli._build_review_context(
        _args(), _repo(), _workspace()
    )
    assert changed_files == ["x"]
    # ADO sections are now rendered PER AGENT, not in the session header.
    assert header.startswith("## Change Under Review")
    assert "## ADO Repository Identity" not in header
    # Identities are threaded out for the per-agent ADO render.
    assert [i.org for i in identities] == ["contoso"]
    # The per-agent render produces Identity + Tool Bindings for the opted-in server.
    block = cli.render_ado_context_for_servers(
        identities,
        ["ado-work-items"],
        ["ado-work-items/wit_get_work_item"],
    )
    assert block.startswith("## ADO Repository Identity (USE THESE FOR MCP TOOL CALLS)")
    assert "## ADO Tool Bindings" in block
    # The context carries the resolved org; the registry builds the ado server.
    assert mcp_ctx.ado_org == "contoso"
    cfg = resolve_mcp_config(["ado-work-items"], mcp_ctx)
    server = cfg["ado-work-items"]
    assert server["args"][2] == "contoso"


def test_local_non_ado_repo_skips_ado(monkeypatch, _stub_local):
    # No ADO identity resolves (e.g. a GitHub repo) -> no identities/mcp.
    monkeypatch.setattr(cli, "resolve_ado_identities", lambda **k: [])
    _, header, mcp_ctx, _inj, _cf, identities, _subject = cli._build_review_context(
        _args(), _repo(), _workspace()
    )
    assert "## ADO Repository Identity" not in header
    assert identities == []
    assert mcp_ctx.ado_org is None
    assert resolve_mcp_config(["ado-work-items"], mcp_ctx) is None

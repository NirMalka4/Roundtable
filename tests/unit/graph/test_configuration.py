"""The named-instance config seam: Configuration + get_configuration.

Pins the ``config-as-named-instance`` contract: the agent graph is loaded as a
lazily-cached :class:`Configuration` (one named instance among eventually many)
instead of eager module globals — consumers read ``get_configuration()`` and its
fields directly.
"""

from __future__ import annotations

import pytest
import yaml

from roundtable.bundle import resolve_bundle, set_config_root
from roundtable.graph.loader import load_config_name
from roundtable.graph.model import Configuration, get_configuration


# ── get_configuration: shape, caching, rejection ─────────────────────────────
def test_get_configuration_default_shape() -> None:
    set_config_root(None)
    try:
        cfg = get_configuration()
        assert cfg.name == "buddies"
        assert cfg.executor == "dag"
        assert cfg.sink == "azure_devops"
        assert len(cfg.entries) > 0
        assert cfg.by_key == {e.key: e for e in cfg.entries}
    finally:
        set_config_root(None)


def test_get_configuration_is_cached() -> None:
    root = resolve_bundle("buddies")
    assert get_configuration(root) is get_configuration(root)


def test_get_configuration_cache_is_scoped_to_bundle_root() -> None:
    inspectorx = get_configuration(resolve_bundle("inspectorx"))
    buddies = get_configuration(resolve_bundle("buddies"))

    assert inspectorx is not buddies
    assert (inspectorx.name, buddies.name) == ("inspectorx", "buddies")
    assert inspectorx.by_key == {entry.key: entry for entry in inspectorx.entries}
    assert buddies.by_key == {entry.key: entry for entry in buddies.entries}
    assert inspectorx.publishing.default_min_severity == "medium"
    assert buddies.publishing.default_min_severity == "low"


# ── Validated file/in-memory construction ────────────────────────────────────
def test_file_and_memory_documents_share_one_validated_configuration_path(tmp_path) -> None:
    document = {
        "name": "synthetic",
        "max_steps": 7,
        "agents": [
            {
                "key": "Source",
                "kind": "source",
                "emoji": "S",
                "terminal": True,
            }
        ],
    }
    graph = tmp_path / "agent_graph.yaml"
    graph.write_text(
        "agents:\n"
        "- emoji: S\n"
        "  key: Source\n"
        "  kind: source\n"
        "  terminal: true\n"
        "max_steps: 7\n"
        "name: synthetic\n",
        encoding="utf-8",
    )

    built = Configuration.from_document(document, root=tmp_path)
    loaded = Configuration.from_file(graph)
    entries = built.entries

    assert built.name == "synthetic"
    assert built.max_steps == 7
    assert built.by_key == {e.key: e for e in entries}
    assert built.terminal_agents == frozenset(e.key for e in entries if e.terminal)
    assert built.non_graph_infra_agents == frozenset(e.key for e in entries if e.non_graph_infra)
    assert loaded == built
    assert loaded.identity["graphConfigSha"] == built.fingerprint


def test_in_memory_configuration_rejects_unvalidated_document_keys(tmp_path) -> None:
    document = {"agents": [{"key": "Source", "emoji": "S", "bogus": True}]}

    with pytest.raises(ValueError, match="bogus"):
        Configuration.from_document(document, root=tmp_path)


@pytest.mark.parametrize("source_kind", ("memory", "file"))
def test_configuration_rejects_duplicate_agent_keys_before_construction(
    tmp_path, source_kind: str
) -> None:
    document = {
        "agents": [
            {"key": "Source", "kind": "source", "emoji": "S"},
            {"key": "Other", "kind": "source", "emoji": "O"},
            {"key": "Source", "kind": "source", "emoji": "D"},
        ]
    }
    graph = tmp_path / "agent_graph.yaml"
    graph.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError) as error:
        if source_kind == "memory":
            Configuration.from_document(document, root=tmp_path)
        else:
            Configuration.from_file(graph)

    assert str(error.value) == (
        "config structure: agents[2].key: duplicate agent key 'Source'; "
        "first declared at agents[0].key"
    )


def test_effective_domain_values_change_fingerprint_and_identity(tmp_path) -> None:
    base = {
        "name": "synthetic",
        "agents": [{"key": "Source", "kind": "source", "emoji": "S"}],
    }
    first = Configuration.from_document(
        {
            **base,
            "domain_values": {"values": {"priority": ["low", "high"]}},
        },
        root=tmp_path,
    )
    second = Configuration.from_document(
        {
            **base,
            "domain_values": {"values": {"priority": ["high", "low"]}},
        },
        root=tmp_path,
    )

    assert first.fingerprint != second.fingerprint
    assert first.identity["domainValues"] == {"priority": ["low", "high"]}
    assert second.identity["domainValues"] == {"priority": ["high", "low"]}


def test_publishing_policy_defaults_to_no_floor_and_changes_fingerprint(tmp_path) -> None:
    base = {
        "name": "synthetic",
        "domain_values": {"values": {"severity": ["low", "high"]}},
        "agents": [{"key": "Source", "kind": "source", "emoji": "S"}],
    }
    unfiltered = Configuration.from_document(base, root=tmp_path)
    filtered = Configuration.from_document(
        {
            **base,
            "publishing": {"default_min_severity": "high"},
        },
        root=tmp_path,
    )

    assert unfiltered.publishing.default_min_severity is None
    assert filtered.publishing.default_min_severity == "high"
    assert filtered.fingerprint != unfiltered.fingerprint


# ── load_config_name: default + explicit key ─────────────────────────────────
def test_load_config_name_defaults_when_key_absent(tmp_path) -> None:
    cfg = tmp_path / "no_name.yaml"
    cfg.write_text("agents: []\n", encoding="utf-8")
    assert load_config_name(cfg) == "inspectorx"


def test_load_config_name_reads_explicit_key(tmp_path) -> None:
    cfg = tmp_path / "named.yaml"
    cfg.write_text("name: my_config\nagents: []\n", encoding="utf-8")
    assert load_config_name(cfg) == "my_config"

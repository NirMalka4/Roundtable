from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtable.bundle import resolve_bundle
from roundtable.graph import Configuration, get_configuration
from roundtable.graph.session_identity import (
    ConfigurationIdentity,
    ConfigurationIdentityError,
    describe_active_configuration,
    require_compatible_fingerprint,
    resolve_session_configuration,
)


def test_new_identity_prefers_stable_shipped_bundle_id() -> None:
    config = get_configuration(resolve_bundle("buddies"))
    identity = describe_active_configuration(config)

    assert identity == {
        "version": 1,
        "name": "buddies",
        "graphConfigSha": config.fingerprint,
        "domainValues": {
            "severity": ["low", "medium", "high"],
            "verdict": ["APPROVE", "APPROVE_WITH_SUGGESTIONS", "REJECT"],
        },
        "kind": "shipped",
        "bundle": "buddies",
    }


def test_external_identity_preserves_resolvable_path(tmp_path: Path) -> None:
    bundle = tmp_path / "external"
    bundle.mkdir()
    config = Configuration.from_document(
        {
            "name": "custom",
            "agents": [{"key": "Source", "kind": "source", "emoji": "S"}],
        },
        root=bundle,
    )
    payload = describe_active_configuration(config)

    assert payload["kind"] == "external"
    assert payload["path"] == str(bundle.resolve())
    assert payload["graphConfigSha"] == config.fingerprint
    assert payload["domainValues"] == {}


def test_replay_rejects_domain_values_that_do_not_match_the_resolved_bundle(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "external"
    bundle.mkdir()
    (bundle / "agent_graph.yaml").write_text(
        "name: custom\n"
        "domain_values:\n"
        "  values:\n"
        "    severity: [low, high]\n"
        "agents:\n"
        "- key: Source\n"
        "  kind: source\n"
        "  emoji: S\n",
        encoding="utf-8",
    )
    configuration = Configuration.from_file(bundle)
    payload = configuration.identity
    payload["domainValues"] = {"severity": ["low", "critical"]}
    (tmp_path / "configuration.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigurationIdentityError, match="domain values differ"):
        resolve_session_configuration(tmp_path)


def test_new_identity_without_fingerprint_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "configuration.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "shipped",
                "bundle": "buddies",
                "name": "buddies",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationIdentityError, match="graphConfigSha"):
        resolve_session_configuration(tmp_path)


def test_legacy_graph_resolves_only_known_shipped_name(tmp_path: Path) -> None:
    (tmp_path / "graph.json").write_text(
        '{"displayName": "buddies", "agents": []}', encoding="utf-8"
    )

    identity = resolve_session_configuration(tmp_path)

    assert identity.name == "buddies"
    assert identity.root.name == "buddies"


def test_unknown_legacy_graph_fails_loud(tmp_path: Path) -> None:
    (tmp_path / "graph.json").write_text(
        json.dumps({"displayName": "not-installed", "agents": []}), encoding="utf-8"
    )

    try:
        resolve_session_configuration(tmp_path)
    except ConfigurationIdentityError as err:
        assert "does not identify exactly one shipped bundle" in str(err)
    else:
        raise AssertionError("an unrelated ambient default must not be selected")


def test_new_session_fingerprint_drift_requires_explicit_override(monkeypatch) -> None:
    identity = ConfigurationIdentity(Path("/bundle"), "custom", "recorded")
    monkeypatch.setattr("roundtable.graph.session_identity.graph_config_sha", lambda *_: "current")

    with pytest.raises(ConfigurationIdentityError, match="--allow-config-drift"):
        require_compatible_fingerprint(identity)

    assert require_compatible_fingerprint(identity, allow_drift=True) == (
        "recorded",
        "current",
    )


def test_legacy_session_has_no_fingerprint_compatibility_block(monkeypatch) -> None:
    identity = ConfigurationIdentity(Path("/bundle"), "legacy", None)
    monkeypatch.setattr(
        "roundtable.graph.session_identity.graph_config_sha",
        lambda *_: (_ for _ in ()).throw(AssertionError("legacy must not hash ambient config")),
    )

    assert require_compatible_fingerprint(identity) is None

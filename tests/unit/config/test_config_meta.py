"""Tests for the config meta-schema (Layer 0 structural validation).

Proves the engine-owned grammar accepts the shipped bundle and rejects the
malformations the tolerant loaders would silently drop: stray/dead keys, wrong
scalar types, and out-of-set enum values. Structural only — semantics belong to
the coherence layers.
"""

from __future__ import annotations

import pathlib

import yaml

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration
from roundtable.graph.meta import validate_config_meta

_CONFIG = get_configuration(resolve_bundle("inspectorx"))


def _write(tmp_path: pathlib.Path, data: dict) -> pathlib.Path:
    p = tmp_path / "agent_graph.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _load_shipped_dict() -> dict:
    return yaml.safe_load((_CONFIG.root / "agent_graph.yaml").read_text(encoding="utf-8"))


def test_shipped_bundle_is_well_formed():
    assert validate_config_meta(_CONFIG.root / "agent_graph.yaml") == []


def test_stray_agent_key_is_rejected(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["bogus_field"] = True
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("bogus_field" in e and "agents[0]" in e for e in errs)


def test_wrong_scalar_type_is_rejected(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["timeout_seconds"] = "soon"
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("timeout_seconds" in e and "integer" in e for e in errs)


def test_legacy_timeout_ms_is_rejected(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["timeout_ms"] = 720000
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("timeout_ms" in e and "additional" in e.lower() for e in errs)


def test_non_positive_timeout_seconds_is_rejected(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["timeout_seconds"] = 0
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("timeout_seconds" in e and "minimum" in e for e in errs)


def test_null_publishing_floor_is_accepted(tmp_path):
    data = _load_shipped_dict()
    data["publishing"] = {"default_min_severity": None}
    assert validate_config_meta(_write(tmp_path, data)) == []


def test_unknown_publishing_key_is_rejected(tmp_path):
    data = _load_shipped_dict()
    data["publishing"] = {"minimum": "medium"}
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("minimum" in error and "publishing" in error for error in errs)


def test_out_of_set_gate_level_is_rejected(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["ovg_gates"] = [{"gate": "json_schema", "level": "fatal"}]
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("level" in e and "fatal" in e for e in errs)


def test_missing_required_agents_is_rejected(tmp_path):
    errs = validate_config_meta(_write(tmp_path, {"name": "x"}))
    assert any("agents" in e for e in errs)


def test_agent_missing_key_is_rejected(tmp_path):
    data = _load_shipped_dict()
    del data["agents"][0]["key"]
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("key" in e for e in errs)


def test_valid_tool_policy_is_accepted(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["tool_policy"] = {
        "powershell": {
            "invocation_cap_seconds": 120,
            "detached_allowed": False,
        },
        "read_powershell": {"poll_cap_seconds": 30},
    }
    assert validate_config_meta(_write(tmp_path, data)) == []


def test_tool_policy_rejects_unknown_keys(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["tool_policy"] = {"powershell": {"timeout_seconds": 120}}
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("timeout_seconds" in error for error in errs)


def test_tool_policy_rejects_non_positive_duration(tmp_path):
    data = _load_shipped_dict()
    data["agents"][0]["tool_policy"] = {"read_powershell": {"poll_cap_seconds": 0}}
    errs = validate_config_meta(_write(tmp_path, data))
    assert any("poll_cap_seconds" in error and "minimum" in error for error in errs)

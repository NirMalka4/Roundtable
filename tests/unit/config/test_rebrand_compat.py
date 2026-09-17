"""Back-compat seams for the InspectorX -> Roundtable engine rebrand.

Each behaviour here is deliberately temporary (``# rebrand-compat``) and exists
only to keep an upgrading user working for one deprecation release:

* legacy ``INSPECTORX_*`` env vars are read when the new ``ROUNDTABLE_*`` name is
  unset (new name still wins when both are set);
* a legacy ``inspectorx.yaml`` is discovered when no ``roundtable.yaml`` exists;
* the legacy ``~/InspectorX-py`` runtime tree is migrated to ``~/roundtable`` once.
"""

from __future__ import annotations

import warnings

from roundtable.bundle import runtime_home
from roundtable.settings.env_compat import (
    LEGACY_CONFIG_FILENAME,
    with_legacy_fallback,
)
from roundtable.settings.workspace import discover_config, load_settings


# ── env fallback: INSPECTORX_* honoured only when ROUNDTABLE_* is unset ──────
def test_legacy_env_key_read_when_new_unset():
    env = with_legacy_fallback({"INSPECTORX_MCP_NPM_REGISTRY": "legacy"})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert env.get("ROUNDTABLE_MCP_NPM_REGISTRY") == "legacy"


def test_new_env_key_wins_over_legacy():
    env = with_legacy_fallback(
        {"ROUNDTABLE_MCP_NPM_REGISTRY": "new", "INSPECTORX_MCP_NPM_REGISTRY": "legacy"}
    )
    assert env.get("ROUNDTABLE_MCP_NPM_REGISTRY") == "new"


def test_legacy_env_flows_through_load_settings(tmp_path):
    s = load_settings(
        start_dir=tmp_path,
        env=with_legacy_fallback({"INSPECTORX_MCP_NPM_REGISTRY": "legacy"}),
    )
    assert s.mcp_npm_registry == "legacy"


# ── config discovery: legacy inspectorx.yaml honoured as a fallback ──────────
def test_legacy_config_filename_discovered(tmp_path):
    (tmp_path / LEGACY_CONFIG_FILENAME).write_text("max_attempts: 3\n", encoding="utf-8")
    assert discover_config(tmp_path) == tmp_path / LEGACY_CONFIG_FILENAME


def test_new_config_preferred_over_legacy(tmp_path):
    (tmp_path / LEGACY_CONFIG_FILENAME).write_text("max_attempts: 3\n", encoding="utf-8")
    (tmp_path / "roundtable.yaml").write_text("max_attempts: 4\n", encoding="utf-8")
    assert discover_config(tmp_path) == tmp_path / "roundtable.yaml"


# ── home migration: ~/InspectorX-py -> ~/roundtable, once, non-destructive ───
def test_migrate_legacy_home_dir_renames(tmp_path, monkeypatch):
    home = tmp_path
    legacy = home / "InspectorX-py"
    (legacy / "clones").mkdir(parents=True)
    (legacy / "clones" / "keep.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(runtime_home.Path, "home", staticmethod(lambda: home))

    runtime_home.migrate_legacy_home_dir()

    new = home / "roundtable"
    assert new.is_dir()
    assert (new / "clones" / "keep.txt").read_text(encoding="utf-8") == "x"


def test_migrate_noop_when_new_tree_exists(tmp_path, monkeypatch):
    home = tmp_path
    (home / "InspectorX-py").mkdir()
    (home / "roundtable").mkdir()
    monkeypatch.setattr(runtime_home.Path, "home", staticmethod(lambda: home))

    runtime_home.migrate_legacy_home_dir()

    # Legacy tree left untouched; nothing migrated over an existing new tree.
    assert (home / "InspectorX-py").is_dir()

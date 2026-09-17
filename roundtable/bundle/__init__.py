"""Configuration-bundle discovery and filesystem locations."""

from .paths import (
    CONFIGS_DIR,
    ENV_VAR,
    ConfigRootError,
    config_root,
    gates_manifest,
    graph_path,
    hint_dir,
    resolve_bundle,
    resolve_config_root,
    schema_dir,
    set_config_root,
    shipped_bundles,
)
from .prompt_bundle import bundle_root
from .runtime_home import home_root, migrate_legacy_home_dir

__all__ = [
    "CONFIGS_DIR",
    "ENV_VAR",
    "ConfigRootError",
    "bundle_root",
    "config_root",
    "gates_manifest",
    "graph_path",
    "hint_dir",
    "home_root",
    "migrate_legacy_home_dir",
    "resolve_bundle",
    "resolve_config_root",
    "schema_dir",
    "set_config_root",
    "shipped_bundles",
]

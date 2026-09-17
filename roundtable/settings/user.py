"""User-level, machine-owned settings that apply outside any workspace."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

from .workspace import ConfigError, validate_update_source

USER_UPDATE_FILENAME = "update.yaml"


def user_update_settings_path(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform: str | None = None,
) -> Path:
    """Return Roundtable's cross-platform, user-level updater settings path."""
    values = os.environ if env is None else env
    user_home = Path.home() if home is None else home
    current_platform = sys.platform if platform is None else platform
    if current_platform == "win32":
        base = Path(values.get("APPDATA") or user_home / "AppData" / "Roaming")
        return base / "Roundtable" / USER_UPDATE_FILENAME
    if current_platform == "darwin":
        return user_home / "Library" / "Application Support" / "Roundtable" / USER_UPDATE_FILENAME
    base = Path(values.get("XDG_CONFIG_HOME") or user_home / ".config")
    return base / "roundtable" / USER_UPDATE_FILENAME


def load_user_update_source(path: Path) -> str | None:
    """Load the source from Roundtable's dedicated updater settings file."""
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise ConfigError(f"user update settings at {path} are not valid YAML: {err}") from err
    if not isinstance(raw, Mapping):
        raise ConfigError(f"user update settings at {path} must be a mapping")
    unknown = set(raw) - {"source"}
    if unknown:
        rendered = ", ".join(sorted(str(key) for key in unknown))
        raise ConfigError(f"user update settings at {path} contain unknown keys: {rendered}")
    source = raw.get("source")
    if source is None:
        return None
    return validate_update_source(str(source), source_name=str(path))

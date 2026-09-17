"""The user-home runtime tree root + a first-run migration off the legacy name.

Every durable runtime artifact (session ``artifacts/``, the blobless ``clones/``
cache, lease dotdirs) lives under ``~/<APP_HOME_DIRNAME>``. This module is the
single place that resolves that root, and the one that renames a pre-rebrand
``~/InspectorX-py`` tree to the new location once, so an upgrading user keeps their
(expensive-to-rebuild) clone cache and artifacts.

# rebrand-compat: drop LEGACY_HOME_DIRNAMES + migrate_legacy_home_dir when the
# deprecation window closes (see Q5).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from roundtable.runtime import APP_HOME_DIRNAME, APP_NAME

# Pre-rebrand runtime-tree directory names, newest legacy first.
LEGACY_HOME_DIRNAMES = ("InspectorX-py",)


def home_root() -> Path:
    """Absolute path to the runtime tree root, ``~/<APP_HOME_DIRNAME>``."""
    return Path.home() / APP_HOME_DIRNAME


def migrate_legacy_home_dir() -> None:
    """Rename a legacy ``~/InspectorX-py`` tree to ``~/<APP_HOME_DIRNAME>`` once.

    No-op when the new tree already exists or no legacy tree is present. On a
    cross-device rename error, falls back to a copy (leaving the legacy tree in
    place). Best-effort: any failure is reported but never aborts the run.
    """
    new = home_root()
    if new.exists():
        return
    for legacy_name in LEGACY_HOME_DIRNAMES:
        legacy = Path.home() / legacy_name
        if not legacy.is_dir():
            continue
        try:
            legacy.rename(new)
        except OSError:
            try:
                shutil.copytree(legacy, new)
            except OSError as err:
                print(
                    f"[{APP_NAME}] could not migrate runtime tree {legacy} -> {new}: {err}",
                    file=sys.stderr,
                )
                return
        print(f"[{APP_NAME}] migrated runtime tree {legacy} -> {new}", file=sys.stderr)
        return

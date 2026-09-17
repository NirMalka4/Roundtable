"""Resolve the active configuration BUNDLE root — the one seam every load site shares.

A *bundle* is one config instance's data: its ``agent_graph.yaml``, ``schemas/**``,
``hints/**``, ``gates.yaml``, and the prompt bundle ``prompts/Reviewer/**``. The
default shipped ``roundtable`` bundle lives at ``roundtable/configs/buddies/``
(relocated out of the ``config/`` python package), so the default root is that
directory. Every hardcoded ``Path(__file__).../schemas`` load site now resolves
through here instead, which is what makes pointing at ANY external bundle a
one-line override.

Precedence (highest first):
1. an explicit override set by the CLI layer (:func:`set_config_root`),
2. the ``ROUNDTABLE_CONFIG_ROOT`` environment variable,
3. the package default (this directory).

Resolution is call-time, so an override set before the first load is honored without
re-importing. The invariant that makes ``--config`` work is that **nothing reads a
bundle at import time** — bundle-derived values are cached, so an import-time read
would bind whichever bundle was default before the CLI ever saw the flag. That is
pinned by ``tests/unit/config/test_paths.py``, not by a runtime guard.
"""

from __future__ import annotations

import os
from pathlib import Path

from roundtable.settings import with_legacy_fallback

ENV_VAR = "ROUNDTABLE_CONFIG_ROOT"

# The shipped bundles live in roundtable/configs/.
CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
_PACKAGE_CONFIG_DIR = CONFIGS_DIR / "buddies"
_override: Path | None = None


class ConfigRootError(RuntimeError):
    """The requested bundle cannot be used as this run's configuration root."""


def shipped_bundles() -> list[str]:
    """The shipped bundle names, i.e. every ``configs/<name>/agent_graph.yaml``."""
    return sorted(p.parent.name for p in CONFIGS_DIR.glob("*/agent_graph.yaml"))


def resolve_bundle(name_or_path: str) -> Path:
    """A shipped bundle name (``buddies``) or a filesystem path → a bundle root.

    Raises :class:`ConfigRootError` naming the shipped bundles when the argument is
    neither — an unusable bundle must fail at the flag, not as a missing-file error
    deep inside a loader.
    """
    shipped = CONFIGS_DIR / name_or_path
    candidate = shipped if shipped.is_dir() else Path(name_or_path).expanduser()
    if not (candidate / "agent_graph.yaml").is_file():
        raise ConfigRootError(
            f"no configuration bundle at {name_or_path!r} "
            f"(expected a shipped name — {', '.join(shipped_bundles())} — "
            "or a directory containing agent_graph.yaml)"
        )
    return candidate.resolve()


def set_config_root(path: str | Path | None) -> None:
    """Set (or clear, with ``None``) the process-wide bundle root — the CLI-flag seam.

    Must run before anything reads the root (see the module docstring).
    """
    global _override
    _override = Path(path).expanduser().resolve() if path is not None else None


def resolve_config_root() -> tuple[Path, str]:
    """The active bundle root and the layer that chose it (``flag:--config`` /
    ``env:ROUNDTABLE_CONFIG_ROOT`` / ``default``)."""
    if _override is not None:
        return _override, "flag:--config"
    env = with_legacy_fallback(os.environ).get(ENV_VAR)
    if env:
        return Path(env).expanduser().resolve(), f"env:{ENV_VAR}"
    return _PACKAGE_CONFIG_DIR, "default"


def config_root() -> Path:
    """The active bundle root: override → ``ROUNDTABLE_CONFIG_ROOT`` → package default."""
    return resolve_config_root()[0]


def graph_path() -> Path:
    """Path to the bundle's ``agent_graph.yaml``."""
    return config_root() / "agent_graph.yaml"


def schema_dir() -> Path:
    """The bundle's ``schemas/`` directory (per-agent + ``_shared/`` schemas)."""
    return config_root() / "schemas"


def hint_dir() -> Path:
    """The bundle's ``hints/`` directory (gate retry-hint markdown)."""
    return config_root() / "hints"


def gates_manifest() -> Path:
    """Path to the bundle's ``gates.yaml`` gate manifest."""
    return config_root() / "gates.yaml"

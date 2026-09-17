"""prompt_bundle: locate the vendored Roundtable prompt bundle.

The prompt bundle (``prompts/Reviewer/``) lives *inside* the active config bundle
(``configs/inspectorx/prompts/Reviewer/``), so it is part of the one consolidated
config directory and moves as a unit when the config root is repointed. Resolution
order:

1. ``settings.prompt_dir`` (``roundtable.yaml`` ``prompt_dir`` or the
   ``ROUNDTABLE_PY_PROMPT_DIR`` env var) — if set and a directory, used as the
   bundle root (a full replacement, for prompt development against a local
   checkout). It must contain ``Agents/`` and ``Shared/`` just like the vendored
   bundle.
2. ``config_root()/prompts/Reviewer`` — the prompt sub-tree of the active config
   bundle (see :mod:`roundtable.bundle.paths`). Pointing the config root at an
   external bundle (``ROUNDTABLE_CONFIG_ROOT``) therefore moves the prompts too.

The default config root is package-relative, so this keeps working when the
package is installed (the bundle ships as package-data — see ``pyproject.toml``).
"""

from __future__ import annotations

from pathlib import Path

from .paths import config_root

_VENDORED_SUBPATH = ("prompts", "Reviewer")


def _vendored_root() -> Path:
    """The prompt sub-tree of the active config bundle (``config_root()/prompts/Reviewer``)."""
    return config_root().joinpath(*_VENDORED_SUBPATH)


def bundle_root() -> Path:
    """Return the prompt-bundle root (the dir containing ``Agents/`` + ``Shared/``)."""
    from roundtable.settings import get_settings

    override = get_settings().prompt_dir
    if override:
        p = Path(override).expanduser()
        if p.is_dir():
            return p
        raise FileNotFoundError(f"prompt_dir override {override!r} is not a directory")
    root = _vendored_root()
    if not root.is_dir():
        raise FileNotFoundError(
            f"prompt bundle not found at {root} (is package-data shipped with the wheel?)"
        )
    return root


def agents_dir() -> Path:
    """Directory holding every ``*.agent.md`` (recursively, incl. ``Specialists/``)."""
    return bundle_root() / "Agents"


def shared_dir() -> Path:
    """Directory holding the ``Shared/*`` reference files."""
    return bundle_root() / "Shared"

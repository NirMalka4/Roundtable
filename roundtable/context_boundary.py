"""Static import-boundary guard for the generic ``context`` package.

``context`` (the prompt-composition + deterministic-node machinery: enrichers,
extractors, injection, builder, git/ADO context) is config-agnostic engine core.
The dependency arrow points *into* it from a config's DOMAIN code — its bundle
plugin registers the review-specific enrichers/extract seams — never out.

The walk itself is shared (:mod:`roundtable.import_boundary`); this module only
declares *what* is guarded.
"""

from __future__ import annotations

from pathlib import Path

from .import_boundary import check_import_boundary, guarded_package_dir

_CORE_PKG = "roundtable.context"


def check_context_import_boundary(context_dir: Path | None = None) -> list[str]:
    """Every import from ``context`` into a config bundle or the review DOMAIN."""
    return check_import_boundary(_CORE_PKG, context_dir or guarded_package_dir("context"))

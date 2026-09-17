"""Severity scale helpers derived from the canonical vocabulary schema.

The vocabulary schema is the single source of truth for severity values. This
module keeps the legacy rank name consumed by the publish layer.
"""

from __future__ import annotations

from roundtable.graph import Configuration

from .vocabulary import severity_levels


def severity_rank_map(configuration: Configuration) -> dict[str, int]:
    """Numeric ranks by lowercase severity (info=0 … critical=4).

    Resolved on first call, never at import: the active bundle is only settled once
    the CLI has parsed ``--config``, so an import-time constant would bind whichever
    bundle happened to be default.
    """
    return {v: i for i, v in enumerate(severity_levels(configuration))}

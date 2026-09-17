"""Static import-boundary guard for the generic ``persistence`` package.

``persistence`` (the session-artifact writer: atomic writes, the neutral
``trace.json`` run record, the analytics ``usage-summary.json``, the graph snapshot,
prompt dumps) is config-agnostic engine core. The dependency arrow points *into* it
from a config's DOMAIN code — the review shell hands it an opaque ``overlay`` +
report + exit code — never out.

The walk itself is shared (:mod:`roundtable.import_boundary`); this module only
declares *what* is guarded.
"""

from __future__ import annotations

from pathlib import Path

from .import_boundary import check_import_boundary, guarded_package_dir

_CORE_PKG = "roundtable.persistence"


def check_persistence_import_boundary(persistence_dir: Path | None = None) -> list[str]:
    """Every import from ``persistence`` into a config bundle or the review DOMAIN."""
    return check_import_boundary(_CORE_PKG, persistence_dir or guarded_package_dir("persistence"))

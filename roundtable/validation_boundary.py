"""Static import-boundary guard for the generic ``validation`` package.

``validation`` is the OVG *mechanism*: neutral diagnostics, the gate registry, the
schema loader, the pipeline runner, and the one task-agnostic ``json_schema``
primitive. The gates that encode a particular review contract belong to the config
bundle that declares them; a bundle registers its callables through
:func:`roundtable.validation.gates.register_gate_function`, so the dependency arrow
points *into* ``validation`` and never out.

That is exactly what this guard proves offline. Without it, "the validation package
is config-agnostic" would be a claim in a docstring — the day someone imports a
bundle's gate back into the engine to fix something quickly, doctor goes red instead.

The walk itself is shared (:mod:`roundtable.import_boundary`); this module only
declares *what* is guarded.
"""

from __future__ import annotations

from pathlib import Path

from .import_boundary import check_import_boundary, guarded_package_dir

_CORE_PKG = "roundtable.validation"


def check_validation_import_boundary(validation_dir: Path | None = None) -> list[str]:
    """Every import from ``validation`` into a config bundle or the review DOMAIN."""
    return check_import_boundary(_CORE_PKG, validation_dir or guarded_package_dir("validation"))

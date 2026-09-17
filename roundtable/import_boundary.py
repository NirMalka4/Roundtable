"""The shared mechanism behind every denylist import-boundary guard.

Several engine packages (``context``, ``persistence``, ``validation``) are generic
core: a config bundle's DOMAIN code depends on them, never the reverse. Each proves
that offline by walking its own ``**.py`` and rejecting imports into the domain
packages. The *walk* is identical in every case — only the package under guard, the
denylist, and the message differ — so it lives here once and each guard is a
three-line declaration.

This is a **denylist** mechanism: a guarded package legitimately imports other
generic engine packages, so listing everything it may touch would be unmaintainable.
It suits a core with many legitimate neighbours; a core with exactly one legitimate
neighbour is better served by an allowlist (see ``runtime/backend_boundary.py``).

Each guard is wired into ``roundtable doctor``, so a decoupling cannot silently rot:
the day someone imports a config bundle back into a generic package, doctor goes red.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

#: Every config bundle. Guarded generic packages must never import a bundle — the
#: whole point of the ``plugins:`` registration seam is that the arrow points inward.
CONFIG_BUNDLES = "roundtable.configs"

#: The review DOMAIN packages.
REVIEW_DOMAIN = (
    "roundtable.ado",
    "roundtable.decision",
    "roundtable.delivery",
    "roundtable.extraction",
    "roundtable.review",
)

#: The denylist every generic engine package shares.
DOMAIN_PKGS: tuple[str, ...] = (CONFIG_BUNDLES, *REVIEW_DOMAIN)


def _module_package(path: Path, root: Path, core_pkg: str) -> str:
    """The dotted package that ``path`` lives in (``__init__`` ⇒ its own package)."""
    rel = path.relative_to(root).with_suffix("")
    parts = [*core_pkg.split("."), *rel.parts]
    return ".".join(parts[:-1])  # drop the module/``__init__`` leaf ⇒ its package


def _resolve_relative(pkg: str, level: int, module: str | None) -> str:
    """Resolve a ``from ... import`` relative target to an absolute dotted module."""
    base = pkg.split(".")[: len(pkg.split(".")) - (level - 1)]
    return ".".join(base + (module.split(".") if module else []))


def _is_forbidden(target: str, forbidden: Sequence[str]) -> bool:
    """True iff an absolute dotted target lands in a denied package."""
    return any(target == pkg or target.startswith(f"{pkg}.") for pkg in forbidden)


def _import_targets(node: ast.AST, pkg: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return [_resolve_relative(pkg, node.level, node.module)]
        return [node.module] if node.module else []
    return []


def _violations_in(path: Path, root: Path, core_pkg: str, forbidden: Sequence[str]) -> list[str]:
    pkg = _module_package(path, root, core_pkg)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(root.parent).as_posix()
    subject = core_pkg.rpartition(".")[2]
    return [
        f"{rel}: forbidden import {target!r} ({subject} must stay domain-agnostic)"
        for node in ast.walk(tree)
        for target in _import_targets(node, pkg)
        if _is_forbidden(target, forbidden)
    ]


def check_import_boundary(
    core_pkg: str,
    root: Path,
    forbidden: Sequence[str] = DOMAIN_PKGS,
) -> list[str]:
    """Every import from ``core_pkg`` into a denied package, as messages.

    ``core_pkg`` is the dotted name of the guarded package and ``root`` its directory;
    an empty result means the boundary holds.
    """
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        violations.extend(_violations_in(path, root, core_pkg, forbidden))
    return violations


def guarded_package_dir(name: str) -> Path:
    """The directory of a top-level ``roundtable`` sub-package under guard."""
    return Path(__file__).resolve().parent / name

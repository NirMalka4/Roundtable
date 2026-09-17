"""Static guard: the shipped **engine** must not carry the old engine identity.

The InspectorX -> Roundtable rebrand renamed the ENGINE (the ``roundtable``
package, its ``roundtable``/``rt`` CLI, and its ``ROUNDTABLE_*`` env vars) while
deliberately KEEPING the config **bundle** identity (``configs/inspectorx/**``
and its ``name: inspectorx`` selector) behind explicit dual-recognizers.

So this guard forbids only the *engine-package/command/env* tokens
(``inspectorx_py``, ``inspectorx-py``, ``INSPECTORX_``) from the shipped engine —
NOT the bundle name ``inspectorx`` (pervasive and legitimate). Every legitimate
residual is a one-release back-compat seam, allow-listed by a
``# rebrand-compat`` marker or by living in a retained compatibility module.

Wired into ``roundtable doctor`` so a re-introduced ``import inspectorx_py`` (or a
stray ``INSPECTORX_`` read) turns doctor red instead of silently un-doing the
rebrand.

# rebrand-compat: retire this guard (and the shims it protects) when the
# deprecation window closes (see Q5).
"""

from __future__ import annotations

import re
from pathlib import Path

# Old engine package / console-command / env-var prefix. Case-insensitive so a
# ``InspectorX-py`` home-dir mention or an ``INSPECTORX_`` read both trip it.
_FORBIDDEN_RE = re.compile(r"inspectorx[_-]py|INSPECTORX_", re.IGNORECASE)

# A line carrying this marker is an intentional back-compat seam.
_ALLOW_MARKER = "rebrand-compat"

# Whole-file allow: modules whose entire body is the legacy-identity shim.
_ALLOW_FILES = frozenset(
    {
        "roundtable/settings/env_compat.py",
        "roundtable/bundle/runtime_home.py",
        "roundtable/rebrand_guard.py",
    }
)

# The retained bundle identity is out of scope; never scan into it.
_BUNDLE_SEGMENT = ("configs", "inspectorx")

_SCAN_SUFFIXES = frozenset({".py", ".md", ".toml"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _in_bundle(rel_parts: tuple[str, ...]) -> bool:
    return any(rel_parts[i : i + 2] == _BUNDLE_SEGMENT for i in range(len(rel_parts) - 1))


def _scan_paths(root: Path) -> list[Path]:
    paths = [p for p in (root / "roundtable").rglob("*") if p.suffix in _SCAN_SUFFIXES]
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        paths.append(pyproject)
    return sorted(paths)


def _violations_in(path: Path, root: Path) -> list[str]:
    rel = path.relative_to(root).as_posix()
    if rel in _ALLOW_FILES or _in_bundle(path.relative_to(root).parts):
        return []
    if "__pycache__" in path.parts:
        return []
    out: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _ALLOW_MARKER in line:
            continue
        if _FORBIDDEN_RE.search(line):
            out.append(
                f"{rel}:{lineno}: stray old-engine identity "
                f"(mark with '# {_ALLOW_MARKER}' if it is an intentional legacy seam)"
            )
    return out


def check_rebrand_guard(root: Path | None = None) -> list[str]:
    """Every shipped-engine reference to the old engine identity, as messages."""
    base = root or _repo_root()
    violations: list[str] = []
    for path in _scan_paths(base):
        violations.extend(_violations_in(path, base))
    return violations

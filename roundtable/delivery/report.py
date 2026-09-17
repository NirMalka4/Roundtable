"""output.report: the pluggable session-report seam — how a config renders ``verdict.md``.

Every review writes a human ``verdict.md``. *What it says* is topology-specific: a
config whose Judge emits ``verdict_overlay`` and one whose Judge emits ``claims`` have
nothing renderable in common beyond the verdict line itself. This module holds the
seam; the renderers live with the shape they read.

Selected by a config's top-level ``report:`` key, resolved by :func:`get_report` —
mirroring the ``executor:`` / ``sink:`` / ``projector:`` seams. Renderers **register
themselves** from the bundle's ``plugins:`` modules (like enrichers and gates), so the
generic core never names a config bundle; ``register_config_plugins`` runs before both
``doctor`` and a review, so a wired name always resolves.

Why this is a seam and not a branch: the report used to be rendered from the *publish
plan*. Publishing is optional and config-owned, so a config that opts out silently lost
its report. Report and publish are now independent — a config may have one, both, or
neither.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from roundtable.decision import VerdictResult


@dataclass(frozen=True)
class ReportResult:
    """One session's rendered report, plus anything the renderer wants surfaced.

    ``integrity_warning`` carries a config's own output-integrity complaint (e.g.
    InspectorX's count-parity check) so the review shell can log it without knowing
    what was checked. ``None`` ⇒ nothing to report.
    """

    markdown: str
    integrity_warning: str | None = None


@runtime_checkable
class Report(Protocol):
    """What a config's ``report:`` key must resolve to."""

    name: str

    def render(
        self,
        verdict: VerdictResult,
        counts: Mapping[str, int] | None,
        session_results: Mapping[str, Any],
        *,
        repo_name: str | None,
        source_branch: str | None,
        session_id: str,
    ) -> ReportResult | None:
        """This session's report, or ``None`` to fall back to the slim verdict stub."""
        ...


_REGISTRY: dict[str, Report] = {}


def register_report(name: str, report: Report) -> None:
    """Register a report renderer under ``name`` (the config's ``report:`` value).

    Raises on a duplicate name — the registry is a static wiring table, so a clash is a
    wiring bug, not something to silently overwrite.
    """
    if name in _REGISTRY:
        raise ValueError(f"report renderer already registered: {name!r}")
    _REGISTRY[name] = report


def report_names() -> frozenset[str]:
    """All registered renderer names (doctor uses this to validate ``report:``)."""
    return frozenset(_REGISTRY)


def get_report(name: str | None) -> Report | None:
    """The renderer a config's ``report:`` key names, or ``None`` when it names none.

    ``None`` (an explicit ``report: null``, or no key at all) is not an error — that
    config renders the slim verdict stub. An unregistered *name* is a loud config
    error naming the known renderers, like an unknown sink or executor.
    """
    if name is None:
        return None
    report = _REGISTRY.get(name)
    if report is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ValueError(f"unknown report renderer {name!r}; known renderers: {known}")
    return report

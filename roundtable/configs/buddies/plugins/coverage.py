"""coverage: which reviewers actually reported, and why the missing ones did not.

A Buddies verdict is an adjudication of six perspectives. When one of them dies the
run still produces a confident-looking verdict over the survivors, and every surface a
human reads — ``verdict.md``, the PR comment — renders exactly what a full run renders.
On PR 123 the Judge approved a change while the test-adequacy reviewer had timed
out, and nothing anywhere said so: "RedGreen found nothing" and "RedGreen never
finished" were indistinguishable.

Coverage is therefore reported *positively*. A reader who has to infer the roster from
the sections that rendered will infer it wrong, because a dead reviewer contributes no
section to notice the absence of.

**The roster is derived, never listed.** It is the graph's finding-producing
non-terminal agents (``finding_producing_agent_keys``), which is the same set the
finding extractor indexes — so a reviewer added to ``agent_graph.yaml`` is covered here
the moment its schema marks a finding array, and one removed stops being reported
missing. A hand-maintained list would be a seventh place to forget.

**The reason is the engine's, not this module's.** ``unreadable_reason`` distinguishes
"never ran" from "timed out" from "spent its whole retry budget failing validation" —
different failures with opposite fixes, and the distinction is worthless if the surface
a human reads flattens them back to "missing".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from roundtable.graph import finding_producing_agent_keys
from roundtable.result_access import unreadable_reason
from roundtable.runtime import get_agent_display_name

from .configuration import buddies_configuration


@dataclass(frozen=True)
class ReviewerCoverage:
    """The roster and the subset of it that delivered nothing readable."""

    expected: tuple[str, ...]
    """Every reviewer the graph declares, in declaration order (display names)."""
    degraded: tuple[tuple[str, str], ...]
    """``(display name, why it produced nothing)`` for each reviewer that did not."""

    @property
    def complete(self) -> bool:
        return not self.degraded

    @property
    def total_blackout(self) -> bool:
        """Every declared reviewer died — there is no review left to adjudicate."""
        return bool(self.expected) and len(self.degraded) == len(self.expected)

    def summary_line(self) -> str:
        """One line a reader can act on, whether or not anything is missing."""
        reported = len(self.expected) - len(self.degraded)
        if self.complete:
            return f"All {len(self.expected)} reviewers reported."
        return (
            f"{reported} of {len(self.expected)} reviewers reported — "
            f"this verdict was reached without {_and_list(n for n, _ in self.degraded)}."
        )


def _and_list(names: Any) -> str:
    """``a``, ``a and b``, ``a, b and c`` — a list a human reads, not a repr."""
    items = list(names)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _reviewer_keys() -> tuple[str, ...]:
    """The declared reviewer roster in graph-declaration order.

    Declaration order rather than set order so two runs of the same graph produce the
    same document.
    """
    configuration = buddies_configuration()
    finding_producers = finding_producing_agent_keys(
        include_terminal=False,
        config=configuration,
    )
    return tuple(e.key for e in configuration.entries if e.key in finding_producers)


def reviewer_coverage(session_results: Mapping[str, Any]) -> ReviewerCoverage:
    """Who was expected, and which of them produced nothing readable and why."""
    expected = _reviewer_keys()
    configuration = buddies_configuration()
    degraded = []
    for key in expected:
        display = get_agent_display_name(key, configuration)
        reason = unreadable_reason(session_results.get(key), agent=display)
        if reason is not None:
            degraded.append((display, reason))
    return ReviewerCoverage(
        expected=tuple(get_agent_display_name(k, configuration) for k in expected),
        degraded=tuple(degraded),
    )


def coverage_markdown(coverage: ReviewerCoverage) -> list[str]:
    """The coverage block for a human surface: the line, then each reason.

    Rendered even when coverage is complete — the absence of a warning is only
    trustworthy if its presence was possible.
    """
    if coverage.complete:
        return [f"**Coverage** — {coverage.summary_line()}", ""]
    lines = [f"> ⚠️ **Degraded coverage** — {coverage.summary_line()}", ">"]
    lines.extend(f"> - {reason}" for _name, reason in coverage.degraded)
    lines.append("")
    return lines

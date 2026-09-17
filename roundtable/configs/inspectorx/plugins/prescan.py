"""prescan: DeterministicPreScan (DPS) — deterministic, offline pre-scan.

DPS runs *before* the LLM graph (it is ``non_graph_infra``) and produces a single
prose ``response`` string that is injected verbatim into its two hard-dep
consumers — ``CodeCorrectness`` and ``SchemaDrift`` — via the standard
``## Context from DeterministicPreScan [REQUIRED]`` section (see
``context/injection.py``).

It scans the *added* diff lines for debug prints / TODO-FIXME / hardcoded-secret
patterns. Cheap, fully offline, deterministic. Always emits a non-empty result (a
"No obvious issues found." line when clean), so a consumer's hard dep is always
satisfied.
"""

from __future__ import annotations

import re

# `(?:password|secret|api_key)\s*[:=]` case-insensitive secret pattern.
_SECRET_RE = re.compile(r"(?:password|secret|api_key)\s*[:=]", re.IGNORECASE)


def extract_added_lines(diff: str) -> str:
    """Added ('+' prefixed, excluding '+++' headers) lines, '+' stripped."""
    return "\n".join(
        line[1:] for line in diff.split("\n") if line.startswith("+") and not line.startswith("+++")
    )


def run_prescan(diff: str) -> str:
    """Run the deterministic base anti-pattern scan over a unified diff.

    Returns the DPS ``response`` prose (always non-empty).
    """
    added = extract_added_lines(diff)
    findings: list[str] = []

    if "console.log" in added or "print(" in added:
        findings.append("- Found debug print statements in added code")
    if "TODO" in added or "FIXME" in added:
        findings.append("- Found TODO/FIXME comments in added code")
    if _SECRET_RE.search(added):
        findings.append("- Found potential hardcoded secrets in added code")

    if findings:
        return "Deterministic Pre-Scan Findings:\n" + "\n".join(findings)
    return "Deterministic Pre-Scan: No obvious issues found."

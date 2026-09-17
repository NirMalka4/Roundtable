"""Destination-neutral publication finding and watermark contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from roundtable.extraction import (
    CodeBlock,
    Exploitability,
    FindingItem,
    NormalizedLocation,
    Remediation,
)


@dataclass(frozen=True)
class GroundingUnit:
    source_agent: str
    trace: tuple[str, ...] = ()
    impact: str | None = None
    exploitability: Exploitability | None = None


@dataclass
class PublishableFinding:
    id: str
    title: str
    description: str
    severity: str
    file_path: str | None
    start_line: int | None
    end_line: int | None
    location_index: int
    total_locations: int
    additional_locations: tuple[NormalizedLocation, ...]
    stable_hash: str
    category: str
    judge_category: str | None
    source_agents: tuple[str, ...]
    evidence: tuple[str, ...] = ()
    fix: CodeBlock | str | None = None
    remediation: Remediation | None = None
    grounding: tuple[GroundingUnit, ...] = ()


def compute_stable_hash(parts: Sequence[Any]) -> str:
    norm = "|".join("" if part is None else str(part).strip().lower() for part in parts)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def watermark_hashed(finding_id: str, stable_hash: str) -> str:
    return f"<!-- Roundtable:{finding_id}:H={stable_hash} -->"


def watermark_plain(finding_id: str) -> str:
    return f"<!-- Roundtable:{finding_id} -->"


def locations_of(finding: FindingItem) -> list[NormalizedLocation]:
    if finding.locations:
        locations = [
            NormalizedLocation(
                file_path=location.file_path if isinstance(location.file_path, str) else None,
                start_line=(location.start_line if isinstance(location.start_line, int) else None),
                end_line=(
                    location.end_line if isinstance(location.end_line, int) else location.start_line
                ),
            )
            for location in finding.locations
            if isinstance(location.file_path, str) or isinstance(location.start_line, int)
        ]
        if locations:
            return locations
    if finding.file or finding.line is not None:
        return [
            NormalizedLocation(
                file_path=finding.file,
                start_line=finding.line,
                end_line=finding.line_end if finding.line_end is not None else finding.line,
            )
        ]
    return []

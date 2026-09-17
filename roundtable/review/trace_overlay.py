"""Review-domain fields merged into the neutral persisted run record."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from roundtable.decision import VerdictResult


class OverlayKey:
    """Namespace of the review-domain ``trace.json`` key names."""

    VERDICT = "verdict"
    VERDICT_ICON = "verdictIcon"
    VERDICT_OVERRIDDEN = "verdictOverridden"

    COUNTS = "counts"
    BLOCKING = "blocking"
    NON_BLOCKING = "nonBlocking"
    ALL = "all"
    SECURITY = "security"

    SUBJECT = "subject"
    DIFF_STAT = "diffStat"
    PROVENANCE = "provenance"

    SUBJECT_MODE = "mode"
    SUBJECT_REPO = "repo"
    SUBJECT_REMOTE_URL = "remoteUrl"
    SUBJECT_PR_ID = "prId"
    SUBJECT_PR_TITLE = "prTitle"
    SUBJECT_TARGET_BRANCH = "targetBranch"
    SUBJECT_SOURCE_BRANCH = "sourceBranch"
    SUBJECT_SOURCE_SHA = "sourceSha"
    SUBJECT_BASE_SHA = "baseSha"


def build_overlay(
    verdict: VerdictResult,
    *,
    counts: Mapping[str, int] | None = None,
    subject: Mapping[str, Any] | None = None,
    diff_stat: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the review-domain overlay merged into ``trace.json``."""
    overlay: dict[str, Any] = {
        OverlayKey.VERDICT: verdict.verdict,
        OverlayKey.VERDICT_ICON: verdict.verdict_icon,
        OverlayKey.VERDICT_OVERRIDDEN: verdict.verdict_overridden,
        OverlayKey.COUNTS: dict(counts) if counts is not None else None,
    }
    if subject is not None:
        overlay[OverlayKey.SUBJECT] = dict(subject)
    if diff_stat is not None:
        overlay[OverlayKey.DIFF_STAT] = dict(diff_stat)
    if provenance is not None:
        overlay[OverlayKey.PROVENANCE] = dict(provenance)
    return overlay

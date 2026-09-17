"""ado.anchor: preflight that decides how a finding attaches to a PR.

Pure and network-free — the caller fetches the PR's iterations and the
changed-file set; this module only decides. Two jobs:

1. **Reviewed-iteration resolution.** Findings anchor on agent/diff line numbers
   that are valid against the *reviewed* PR iteration. Rather than gate on a
   coarse SHA comparison, we resolve the reviewed source SHA (persisted in
   ``trace.json``'s ``pr`` block) to its PR **iteration ordinal**, which the caller
   attaches to each inline thread so ADO auto-tracks it forward to head. When the
   SHA can't be mapped to an iteration, the caller downgrades to a general thread
   with an enumerated reason — it never fails and never fabricates an anchor.

2. **Per-finding classification (E7).** Normalize the path/range and decide
   whether a finding can post as an **inline** thread (it names a file in the PR's
   changed set with a valid start line) or must fall back to a **general**
   (PR-level) thread. No inline anchor is ever fabricated for a file the PR did
   not touch or for a missing/invalid line.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .pr_iterations import IterationRef
from .publish import PublishableFinding


@dataclass(frozen=True)
class IterationResolution:
    """Outcome of resolving the reviewed SHA to a PR iteration ordinal.

    ``ordinal`` is set (and ``reason`` is ``None``) on a successful match; on any
    failure ``ordinal`` is ``None`` and ``reason`` is a human-readable explanation
    for the terminal warning.
    """

    ordinal: int | None
    reason: str | None = None


class RelatednessStatus(StrEnum):
    """How confidently the session's findings relate to the target PR.

    * ``RELATED_ANCHORED`` — the reviewed commit is one of the PR's iterations;
      inline comments anchor and ADO auto-tracks them forward.
    * ``RELATED_ROLLED_OFF`` — the reviewed commit is gone from the iteration list
      but the source *branch* still matches: a force-push/superseded review. Post
      as general threads with an informational warning.
    * ``UNCONFIRMABLE`` — a required signal is missing (iterations unfetchable /
      offline, no reviewed commit recorded, branch unknown, PR ref unfetchable).
      Relatedness can be neither proven nor disproven: downgrade, **never block**.
    * ``UNRELATED`` — positively proven wrong PR: the reviewed commit is absent
      from the iterations *and* the source branches are known and differ.
    """

    RELATED_ANCHORED = "related_anchored"
    RELATED_ROLLED_OFF = "related_rolled_off"
    UNCONFIRMABLE = "unconfirmable"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class Relatedness:
    """Verdict on whether a session's review relates to the target PR.

    ``ordinal`` is the reviewed iteration ordinal (set only for
    ``RELATED_ANCHORED``, feeds inline anchoring). ``reason`` is a human-readable
    line — an informational warning for the downgrade states, or the actionable
    block message for ``UNRELATED``.
    """

    status: RelatednessStatus
    ordinal: int | None = None
    reason: str | None = None


def sha_matches(candidate: str | None, target: str) -> bool:
    """Case-insensitive, prefix-tolerant SHA equality (short vs full both match)."""
    cand = (candidate or "").strip().lower()
    if not cand:
        return False
    return cand == target or cand.startswith(target) or target.startswith(cand)


def normalize_branch(ref: str | None) -> str | None:
    """Normalize a branch ref for comparison.

    Strips surrounding whitespace and a leading ``refs/heads/`` so a PR's
    ``sourceRefName`` (``refs/heads/user/x/foo``) compares equal to a session's
    persisted short branch (``user/x/foo``). Returns ``None`` for empty input.
    """
    if not ref:
        return None
    cleaned = ref.strip()
    if cleaned.startswith("refs/heads/"):
        cleaned = cleaned[len("refs/heads/") :]
    return cleaned or None


@dataclass(frozen=True)
class AnchorDecision:
    """How a single finding should attach to the PR."""

    kind: str  # 'inline' | 'general'
    file_path: str | None
    start_line: int | None
    end_line: int | None
    downgrade_reason: str | None = None  # set only when kind == 'general'


def normalize_repo_path(path: str | None) -> str | None:
    """Normalize a repo-relative path for comparison and for the ADO ``filePath``.

    Strips surrounding whitespace, a leading ``a/``/``b/`` diff prefix, and any
    leading slashes. Returns ``None`` for an empty/whitespace path.
    """
    if not path:
        return None
    cleaned = path.strip()
    if cleaned[:2] in ("a/", "b/"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/")
    return cleaned or None


def resolve_reviewed_iteration(
    iterations: Sequence[IterationRef],
    reviewed_sha: str | None,
) -> IterationResolution:
    """Map the reviewed source SHA to its PR iteration ordinal.

    * ``reviewed_sha`` falsy ⇒ pre-provenance session: no reviewed commit recorded.
    * no iteration's source SHA matches ⇒ the reviewed commit is no longer a PR
      iteration (source history was reset/force-pushed, or iterations were
      unavailable).
    * match ⇒ the iteration ordinal, ready to attach for server-side auto-tracking.

    SHA comparison is case-insensitive and prefix-tolerant (a persisted short SHA
    matches the iteration's full SHA and vice versa).
    """
    if not reviewed_sha:
        return IterationResolution(
            None,
            "this session recorded no reviewed commit (pre-provenance session)",
        )
    target = reviewed_sha.strip().lower()
    for it in iterations:
        if sha_matches(it.source_commit_sha, target):
            return IterationResolution(it.ordinal, None)
    return IterationResolution(
        None,
        f"reviewed commit {target[:12]} no longer maps to a PR iteration "
        "(source history was reset/force-pushed, or iterations were unavailable)",
    )


def assess_relatedness(
    *,
    reviewed_sha: str | None,
    reviewed_branch: str | None,
    iterations: Sequence[IterationRef] | None,
    pr_source_ref: str | None,
    pr_display: str,
) -> Relatedness:
    """Decide whether a session's review relates to the target PR.

    Pure and network-free — the caller performs the (lazy) fetches and passes the
    results. ``iterations is None`` means the iterations fetch failed/was skipped;
    ``pr_source_ref is None`` means the PR's source branch is unknown (fetch failed
    or not attempted). See :class:`RelatednessStatus` for the verdict semantics.

    **Blocks (``UNRELATED``) only on positive proof**: the reviewed commit was
    successfully looked up against the iterations and is absent, *and* both source
    branches are known and differ. Any missing signal yields ``UNCONFIRMABLE`` so
    the caller downgrades rather than blocks.
    """
    if not reviewed_sha:
        return Relatedness(
            RelatednessStatus.UNCONFIRMABLE,
            None,
            f"this session recorded no reviewed commit (pre-provenance session) — "
            f"cannot confirm it relates to {pr_display}; posting as general threads",
        )
    if iterations is None:
        return Relatedness(
            RelatednessStatus.UNCONFIRMABLE,
            None,
            f"could not fetch {pr_display} iterations — cannot confirm relatedness; "
            "posting as general threads",
        )

    target = reviewed_sha.strip().lower()
    for it in iterations:
        if sha_matches(it.source_commit_sha, target):
            return Relatedness(RelatednessStatus.RELATED_ANCHORED, it.ordinal, None)

    reviewed = normalize_branch(reviewed_branch)
    pr_branch = normalize_branch(pr_source_ref)
    if reviewed and pr_branch:
        if reviewed == pr_branch:
            return Relatedness(
                RelatednessStatus.RELATED_ROLLED_OFF,
                None,
                f"reviewed commit {target[:12]} rolled off {pr_display} "
                f"(branch {pr_branch} advanced/was force-pushed since review); "
                "posting as general threads",
            )
        return Relatedness(
            RelatednessStatus.UNRELATED,
            None,
            f"this session reviewed branch {reviewed} @ {target[:12]}, but "
            f"{pr_display} is for branch {pr_branch} — the findings do not relate "
            "to this PR. Re-run publish against the correct PR, or re-review the "
            "current PR head.",
        )
    return Relatedness(
        RelatednessStatus.UNCONFIRMABLE,
        None,
        f"reviewed commit {target[:12]} is not an iteration of {pr_display} and "
        "the source branch could not be compared — cannot confirm relatedness; "
        "posting as general threads",
    )


def _normalize_changed_files(changed_files: Iterable[str]) -> frozenset[str]:
    return frozenset(norm for f in changed_files if (norm := normalize_repo_path(f)) is not None)


def classify(
    finding: PublishableFinding,
    changed_files: Iterable[str],
) -> AnchorDecision:
    """Decide whether ``finding`` posts inline or downgrades to a general thread.

    Inline requires: a normalizable file path, membership in the PR's changed
    set, and a start line ``>= 1``. Any miss downgrades to a general thread with a
    human-readable reason (surfaced in the comment's ``**Location:**`` line).
    """
    changed = _normalize_changed_files(changed_files)
    path = normalize_repo_path(finding.file_path)

    if path is None:
        return AnchorDecision("general", None, None, None, "finding has no file location")
    if path not in changed:
        return AnchorDecision("general", path, None, None, "file is not in the PR's changed set")
    start = finding.start_line
    if start is None or start < 1:
        return AnchorDecision("general", path, None, None, "finding has no valid start line")
    end = finding.end_line
    if end is None or end < start:
        end = start
    return AnchorDecision("inline", path, start, end, None)

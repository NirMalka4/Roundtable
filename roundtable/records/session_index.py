"""records.session_index: the per-repo *derived* session index.

Each review persists a self-describing ``trace.json`` (its ``subject`` block records
repo / branch / PR / source SHA — see :mod:`roundtable.review.trace_overlay`). This module turns
a repo's session directories into one ``index.json`` so an operator can tell two
reviews of the *same* branch/PR apart (they differ by ``sourceSha`` and the timestamp
baked into the session id) and re-publish any past session at any time.

The index is **derived, never authoritative**: it is rebuilt in full by scanning
``<repo_dir>/*/trace.json`` and atomically replaced, so there is no read-modify-write
race between concurrent reviews and no second source of truth to drift. Delete it and
:func:`write_repo_index` reconstructs it byte-for-byte from the session traces.

It reads both the neutral run record (:class:`~roundtable.persistence.trace_keys.TraceKey`)
and the opaque review-domain overlay (:class:`~roundtable.review.trace_overlay.OverlayKey`).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from roundtable.persistence import TraceKey as K
from roundtable.persistence import write_atomic

# Index-only key (a row points at its session directory; not a trace.json field).
_DIR = "dir"
_REVIEWS = "reviews"

# Per-session-directory trace filename (kept in step with persistence.trace).
_TRACE_FILENAME = "trace.json"
_INDEX_FILENAME = "index.json"

# Subject fields lifted verbatim into each row (present ⇒ copied; absent ⇒ omitted so
# a legacy trace with no subject block still yields a minimal, valid row).
# Timestamp format written by ``persistence.trace._utc_iso`` (used to derive duration).
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _overlay_keys():
    """Resolve review wire keys lazily so importing records does not initialize review."""
    from roundtable.review import OverlayKey

    return OverlayKey


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    """Whole-review wall time in seconds, or ``None`` when either stamp is unusable.

    Best-effort: a missing/malformed timestamp yields ``None`` rather than breaking the
    index rebuild (which must never fail on one odd trace).
    """
    if not isinstance(started_at, str) or not isinstance(finished_at, str):
        return None
    try:
        start = datetime.strptime(started_at, _TS_FORMAT)
        end = datetime.strptime(finished_at, _TS_FORMAT)
    except ValueError:
        return None
    return round((end - start).total_seconds(), 3)


def _row_from_trace(session_dir_name: str, trace: dict[str, Any]) -> dict[str, Any]:
    """One index row: the session's identity + verdict + timing/cost + flattened subject.

    Carries exactly what discovery/re-publish needs — the directory to open, which
    branch/PR/commit it reviewed, the verdict, and cheap observability (wall time,
    output tokens, and raw SDK billing) — so no consumer has to re-open every
    ``trace.json`` to list or triage a repo's reviews.
    """
    overlay = _overlay_keys()
    row: dict[str, Any] = {
        _DIR: session_dir_name,
        K.SESSION_ID: trace.get(K.SESSION_ID, session_dir_name),
        overlay.VERDICT: trace.get(overlay.VERDICT),
        overlay.VERDICT_ICON: trace.get(overlay.VERDICT_ICON),
        K.AGENT_COUNT: trace.get(K.AGENT_COUNT),
        K.STARTED_AT: trace.get(K.STARTED_AT),
        K.FINISHED_AT: trace.get(K.FINISHED_AT),
    }
    duration = _duration_seconds(trace.get(K.STARTED_AT), trace.get(K.FINISHED_AT))
    if duration is not None:
        row[K.DURATION_SECONDS] = duration
    counts = trace.get(overlay.COUNTS)
    if counts is not None:
        row[overlay.COUNTS] = counts
    usage = trace.get(K.USAGE)
    if isinstance(usage, dict):
        for key in (K.OUTPUT_TOKENS, K.BILLING, K.PREMIUM_REQUESTS):
            if key in usage:
                row[key] = usage[key]
    subject = trace.get(overlay.SUBJECT)
    if isinstance(subject, dict):
        for key in (
            overlay.SUBJECT_MODE,
            overlay.SUBJECT_REPO,
            overlay.SUBJECT_REMOTE_URL,
            overlay.SUBJECT_PR_ID,
            overlay.SUBJECT_PR_TITLE,
            overlay.SUBJECT_TARGET_BRANCH,
            overlay.SUBJECT_SOURCE_BRANCH,
            overlay.SUBJECT_SOURCE_SHA,
            overlay.SUBJECT_BASE_SHA,
        ):
            if key in subject:
                row[key] = subject[key]
    return row


def build_repo_index(repo_dir: Path) -> dict[str, Any]:
    """Scan ``<repo_dir>/*/trace.json`` and build the index dict (pure I/O read).

    Rows are ordered by session-directory name; because the session id is
    timestamp-leading, that ordering is chronological. Directories without a
    readable/parseable ``trace.json`` are skipped (best-effort — a half-written or
    non-session sibling never breaks index generation).
    """
    reviews: list[dict[str, Any]] = []
    if repo_dir.is_dir():
        for child in sorted(repo_dir.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            trace_path = child / _TRACE_FILENAME
            if not trace_path.is_file():
                continue
            try:
                trace = json.loads(trace_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(trace, dict):
                reviews.append(_row_from_trace(child.name, trace))
    return {_overlay_keys().SUBJECT_REPO: repo_dir.name, _REVIEWS: reviews}


def write_repo_index(repo_dir: Path) -> Path:
    """Rebuild and atomically write ``<repo_dir>/index.json``; return its path."""
    index_path = repo_dir / _INDEX_FILENAME
    index = build_repo_index(repo_dir)
    write_atomic(index_path, json.dumps(index, indent=2, ensure_ascii=False))
    return index_path

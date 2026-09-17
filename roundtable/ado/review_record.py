"""Best-effort Azure DevOps recording for one successful PR review."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roundtable import __version__
from roundtable.adoption import (
    ReviewRecord,
    encode_label,
    encode_metadata_marker,
    finding_records,
    installation_source,
)
from roundtable.delivery import get_projector
from roundtable.graph import Configuration
from roundtable.inputs import PrReference
from roundtable.review import OverlayKey, ReviewResult

from .pr_labels import PrLabelClient, stamp_adoption_label
from .pr_threads import PostResult

RECORD_FILENAME = "review-record.json"


@dataclass(frozen=True)
class ReviewRecordReport:
    ok: bool
    record: ReviewRecord
    label: str
    summary: PostResult | None
    label_action: str
    failure_reason: str | None = None


def build_review_record(
    result: ReviewResult,
    pr: PrReference,
    subject: Mapping[str, Any],
    configuration: Configuration,
    *,
    now: datetime | None = None,
    source: str | None = None,
) -> ReviewRecord:
    identity = configuration.identity
    projected = None
    if configuration.projector is not None:
        projected = get_projector(configuration.projector).project(
            result.scheduler.results,
            session_dir_path=str(result.persist.session_dir),
        )
    findings_available = projected is not None
    findings = finding_records(projected.all_findings if projected is not None else [])
    source_counts = projected.counts if projected is not None else result.counts
    counts = tuple(sorted((str(key), int(value)) for key, value in source_counts.items()))
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ReviewRecord(
        session_id=result.persist.session_dir.name,
        recorded_at=timestamp,
        organization=pr.org,
        project=pr.project,
        repository=pr.repo_name,
        pull_request_id=pr.pr_id,
        source_sha=str(subject.get(OverlayKey.SUBJECT_SOURCE_SHA) or ""),
        base_sha=str(subject.get(OverlayKey.SUBJECT_BASE_SHA) or ""),
        tool_version=__version__,
        configuration_name=configuration.name,
        configuration_kind=str(identity["kind"]),
        graph_config_sha=configuration.fingerprint,
        installation_source=source or installation_source(),
        verdict=result.verdict.verdict,
        findings_available=findings_available,
        counts=counts,
        findings=findings,
    )


def append_metadata(
    summary: str,
    record: ReviewRecord,
    *,
    session_reference: str | None = None,
) -> str:
    published_record = (
        replace(record, session_id=session_reference) if session_reference is not None else record
    )
    marker = encode_metadata_marker(published_record)
    without_existing = summary.split("\n<!-- Roundtable-Metadata:v1:", 1)[0].rstrip()
    return f"{without_existing}\n\n{marker}"


def _write_state(
    session_dir: Path,
    record: ReviewRecord,
    label: str,
    *,
    status: str,
    summary: PostResult | None = None,
    label_action: str = "pending",
    failure_reason: str | None = None,
) -> None:
    payload = {
        "record": record.to_dict(),
        "label": label,
        "status": status,
        "summary": (
            {
                "status": summary.status,
                "threadId": summary.thread_id,
                "detail": summary.detail,
            }
            if summary is not None
            else None
        ),
        "labelAction": label_action,
        "failureReason": failure_reason,
    }
    target = session_dir / RECORD_FILENAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(target)


def load_review_record(session_dir: str | Path) -> ReviewRecord | None:
    path = Path(session_dir) / RECORD_FILENAME
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ReviewRecord.from_dict(payload.get("record"))


def record_review(
    record: ReviewRecord,
    pr: PrReference,
    session_dir: str | Path,
    *,
    label_client_factory: Callable[[PrReference], PrLabelClient] | None = None,
) -> ReviewRecordReport:
    root = Path(session_dir)
    label = encode_label(
        record.tool_version,
        record.configuration_name,
        record.installation_source,
        graph_config_sha=record.graph_config_sha,
    )
    _write_state(root, record, label, status="pending")
    try:
        _, label_action = stamp_adoption_label(
            pr,
            label,
            client_factory=label_client_factory,
        )
    except (RuntimeError, OSError, ValueError) as err:
        failure = f"label recording failed ({type(err).__name__})"
        _write_state(root, record, label, status="failed", failure_reason=failure)
        return ReviewRecordReport(False, record, label, None, "pending", failure)
    ok = label_action != "failed"
    failure = None if ok else "label recording failed"
    _write_state(
        root,
        record,
        label,
        status="complete" if ok else "failed",
        label_action=label_action,
        failure_reason=failure,
    )
    return ReviewRecordReport(ok, record, label, None, label_action, failure)


def retry_record(
    session_dir: str | Path,
    *,
    label_client_factory: Callable[[PrReference], PrLabelClient] | None = None,
) -> ReviewRecordReport:
    root = Path(session_dir)
    payload = json.loads((root / RECORD_FILENAME).read_text(encoding="utf-8"))
    record = ReviewRecord.from_dict(payload.get("record"))
    pr = PrReference(
        org=record.organization,
        project=record.project,
        repo_name=record.repository,
        pr_id=record.pull_request_id,
    )
    return record_review(
        record,
        pr,
        root,
        label_client_factory=label_client_factory,
    )

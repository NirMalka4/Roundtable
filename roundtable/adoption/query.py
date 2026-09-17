"""Deterministic dependency-free aggregation over adoption JSONL."""

from __future__ import annotations

import json
from pathlib import Path

from .model import ReviewRecord

_REVIEW_GROUPS = {
    "project",
    "repository",
    "configurationName",
    "installationSource",
    "toolVersion",
    "verdict",
}
_FINDING_GROUPS = {"severity", "category", "agent"}


def _review_group(record: ReviewRecord, group_by: str) -> str:
    return {
        "project": record.project,
        "repository": record.repository,
        "configurationName": record.configuration_name,
        "installationSource": record.installation_source,
        "toolVersion": record.tool_version,
        "verdict": record.verdict,
    }[group_by]


def read_jsonl(path: str | Path) -> tuple[ReviewRecord, ...]:
    records: list[ReviewRecord] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as err:
            raise ValueError(f"invalid JSONL at line {line_number}: {err}") from err
        if isinstance(payload, dict) and "diagnostic" in payload:
            continue
        records.append(ReviewRecord.from_dict(payload))
    return tuple(records)


def query_records(
    records: tuple[ReviewRecord, ...], metric: str, group_by: str
) -> dict[str, object]:
    if metric not in {"prs", "reviews", "findings"}:
        raise ValueError("metric must be prs, reviews, or findings")
    allowed = _FINDING_GROUPS if metric == "findings" else _REVIEW_GROUPS
    if group_by not in allowed:
        raise ValueError(f"{metric} can only group by {', '.join(sorted(allowed))}")
    totals: dict[str, int] = {}
    pull_requests: dict[str, set[tuple[str, str, str, int]]] = {}
    for record in records:
        if metric in {"prs", "reviews"}:
            key = _review_group(record, group_by)
            if metric == "prs":
                pull_requests.setdefault(key, set()).add(
                    (
                        record.organization,
                        record.project,
                        record.repository,
                        record.pull_request_id,
                    )
                )
                continue
            totals[key] = totals.get(key, 0) + 1
            continue
        for finding in record.findings:
            keys = {
                "severity": (finding.severity,),
                "category": (finding.category,),
                "agent": finding.agents,
            }[group_by]
            for key in keys:
                totals[key] = totals.get(key, 0) + 1
    if metric == "prs":
        totals = {key: len(values) for key, values in pull_requests.items()}
    return {
        "metric": metric,
        "groupBy": group_by,
        "groups": [
            {"key": key, "value": totals[key]}
            for key in sorted(totals, key=lambda item: (-totals[item], item.casefold()))
        ],
    }


def query_jsonl(path: str | Path, metric: str, group_by: str) -> dict[str, object]:
    return query_records(read_jsonl(path), metric, group_by)


def render_table(result: dict[str, object]) -> str:
    rows = result["groups"]
    assert isinstance(rows, list)
    lines = [f"{result['groupBy']}\t{result['metric']}"]
    lines.extend(f"{row['key']}\t{row['value']}" for row in rows if isinstance(row, dict))
    return "\n".join(lines)

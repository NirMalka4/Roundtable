"""Azure DevOps adoption collection and audit mechanics."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Protocol, TextIO

from roundtable.ado_client import ado_auth_header, build_ado_base_url, encode_segment
from roundtable.adoption import (
    CollectionResult,
    ReviewRecord,
    parse_label,
    parse_metadata_markers,
)

_INSPECTION_WORKERS = 8
_ERROR_LIMIT = 300


def adoption_record_url(record: ReviewRecord) -> str:
    base_url = build_ado_base_url("dev.azure.com", record.organization)
    project = encode_segment(record.project)
    repository = encode_segment(record.repository)
    return f"{base_url}/{project}/_git/{repository}/pullrequest/{record.pull_request_id}"


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]: ...


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]:
        request = urllib.request.Request(url, method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", errors="replace")


@dataclass
class CollectionCounts:
    prs: int = 0
    labels: int = 0
    matched: int = 0
    threads: int = 0
    records: int = 0
    diagnostics: int = 0

    def add(self, page: _CollectionPage) -> None:
        self.prs += page.prs
        self.labels += page.labels
        self.matched += page.matched
        self.threads += page.threads
        self.records += len(page.records)
        self.diagnostics += len(page.diagnostics)

    def to_dict(self) -> dict[str, int]:
        return {
            "prs": self.prs,
            "labels": self.labels,
            "matched": self.matched,
            "threads": self.threads,
            "records": self.records,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True)
class _Inspection:
    pr_id: int
    records: tuple[ReviewRecord, ...]
    diagnostics: tuple[dict[str, object], ...]
    matched: int
    threads: int


@dataclass(frozen=True)
class _CollectionPage:
    records: tuple[ReviewRecord, ...]
    diagnostics: tuple[dict[str, object], ...]
    prs: int
    labels: int
    matched: int
    threads: int


def _parse_bound(value: str | None, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    suffix = "T23:59:59+00:00" if end else "T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(value if "T" in value else value + suffix)
    except ValueError as err:
        raise ValueError(f"invalid date/time {value!r}") from err
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _recorded_at(record: ReviewRecord) -> datetime:
    return datetime.fromisoformat(record.recorded_at.replace("Z", "+00:00")).astimezone(UTC)


class AzureDevOpsAdoptionCollector:
    def __init__(
        self,
        org: str,
        project: str,
        *,
        host: str = "dev.azure.com",
        transport: Transport | None = None,
        timeout: float = 30.0,
        page_size: int = 100,
        auth_resolver: Callable[[], str | None] = ado_auth_header,
    ) -> None:
        auth = auth_resolver()
        if not auth:
            raise RuntimeError(
                "Collection requires ADO credentials with Code (read). Set "
                "ROUNDTABLE_ADO_PAT/AZURE_DEVOPS_PAT or sign in with 'az login'."
            )
        self._org = org
        self._project = project
        self._base = build_ado_base_url(host, org)
        self._transport = transport or UrllibTransport()
        self._timeout = timeout
        self._page_size = page_size
        self._headers = {"Authorization": auth, "Content-Type": "application/json"}

    def _get(self, url: str) -> dict:
        status, body = self._transport.request(
            "GET", url, headers=self._headers, body=None, timeout=self._timeout
        )
        if status != 200:
            raise RuntimeError(f"ADO collection GET failed (HTTP {status})")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise RuntimeError("ADO collection response was not an object")
        return payload

    def _paged(self, endpoint: str, query: str) -> Iterator[list[dict]]:
        skip = 0
        previous_full_page: str | None = None
        while True:
            separator = "&" if query else ""
            payload = self._get(f"{endpoint}?{query}{separator}$top={self._page_size}&$skip={skip}")
            page = [item for item in payload.get("value") or [] if isinstance(item, dict)]
            if len(page) == self._page_size:
                fingerprint = json.dumps(page, sort_keys=True, separators=(",", ":"))
                if fingerprint == previous_full_page:
                    raise RuntimeError("ADO collection pagination repeated a full page")
                previous_full_page = fingerprint
            if page:
                yield page
            if len(page) < self._page_size:
                return
            skip += len(page)

    def collect(
        self,
        *,
        repository: str | None = None,
        pull_request_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> CollectionResult:
        records: list[ReviewRecord] = []
        diagnostics: list[dict[str, object]] = []
        for page in self.pages(
            repository=repository,
            pull_request_id=pull_request_id,
            since=since,
            until=until,
        ):
            records.extend(page.records)
            diagnostics.extend(page.diagnostics)
        return CollectionResult(tuple(records), tuple(diagnostics))

    def pages(
        self,
        *,
        repository: str | None = None,
        pull_request_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> Iterator[_CollectionPage]:
        if pull_request_id is not None and repository is None:
            raise ValueError("--pr requires --repository")
        lower = _parse_bound(since)
        upper = _parse_bound(until, end=True)
        seen_sessions: set[str] = set()
        for repo in self._repositories(repository):
            yield from self._repository_pages(
                repo,
                pull_request_id=pull_request_id,
                lower=lower,
                upper=upper,
                seen_sessions=seen_sessions,
            )

    def _repositories(self, repository: str | None) -> Iterator[dict]:
        project = encode_segment(self._project)
        endpoint = f"{self._base}/{project}/_apis/git/repositories"
        if repository is not None:
            yield self._get(f"{endpoint}/{encode_segment(repository)}?api-version=7.1")
            return
        for page in self._paged(endpoint, "api-version=7.1"):
            yield from sorted(page, key=lambda item: str(item.get("name", "")).casefold())

    def _repository_pages(
        self,
        repo: dict,
        *,
        pull_request_id: int | None,
        lower: datetime | None,
        upper: datetime | None,
        seen_sessions: set[str],
    ) -> Iterator[_CollectionPage]:
        repo_id = str(repo.get("id") or repo.get("name") or "")
        repo_name = str(repo.get("name") or repo_id)
        project = encode_segment(self._project)
        endpoint = (
            f"{self._base}/{project}/_apis/git/repositories/{encode_segment(repo_id)}/pullrequests"
        )
        for prs in self._pull_request_pages(endpoint, pull_request_id):
            inspections = self._inspect_page(repo_name, endpoint, prs, lower, upper)
            yield _build_page(inspections, seen_sessions)

    def _pull_request_pages(
        self, endpoint: str, pull_request_id: int | None
    ) -> Iterator[list[dict]]:
        if pull_request_id is not None:
            yield [self._get(f"{endpoint}/{pull_request_id}?api-version=7.1")]
            return
        yield from self._paged(endpoint, "searchCriteria.status=all&api-version=7.1")

    def _inspect_page(
        self,
        repository: str,
        endpoint: str,
        prs: list[dict],
        lower: datetime | None,
        upper: datetime | None,
    ) -> tuple[_Inspection, ...]:
        inspect = partial(
            self._inspect_pr,
            repository=repository,
            endpoint=endpoint,
            lower=lower,
            upper=upper,
        )
        with ThreadPoolExecutor(max_workers=_INSPECTION_WORKERS) as executor:
            inspections = executor.map(inspect, prs)
            return tuple(sorted(inspections, key=lambda item: item.pr_id))

    def _inspect_pr(
        self,
        pr: dict,
        *,
        repository: str,
        endpoint: str,
        lower: datetime | None,
        upper: datetime | None,
    ) -> _Inspection:
        pr_id = int(pr.get("pullRequestId", 0))
        labels = self._get(f"{endpoint}/{pr_id}/labels?api-version=7.1-preview.1")
        if not _has_roundtable_label(labels.get("value") or []):
            return _Inspection(pr_id, (), (), 0, 0)
        threads = self._get(f"{endpoint}/{pr_id}/threads?api-version=7.1")
        records, diagnostics = self._parse_threads(
            repository,
            pr_id,
            threads.get("value") or [],
            lower,
            upper,
        )
        return _Inspection(pr_id, records, diagnostics, 1, 1)

    def _parse_threads(
        self,
        repository: str,
        pr_id: int,
        threads: object,
        lower: datetime | None,
        upper: datetime | None,
    ) -> tuple[tuple[ReviewRecord, ...], tuple[dict[str, object], ...]]:
        records: list[ReviewRecord] = []
        diagnostics: list[dict[str, object]] = []
        for content in _metadata_comments(threads):
            try:
                parsed_records = parse_metadata_markers(content)
            except ValueError as err:
                diagnostics.append(_diagnostic(repository, pr_id, "malformed-metadata", str(err)))
                continue
            for record in parsed_records:
                diagnostic = self._validate_record(record, repository, pr_id)
                if diagnostic is not None:
                    diagnostics.append(diagnostic)
                elif _within_bounds(record, lower, upper):
                    records.append(record)
        if not records:
            diagnostics.append(
                _diagnostic(
                    repository,
                    pr_id,
                    "metadata-missing",
                    "current adoption label has no versioned review metadata",
                )
            )
        return tuple(_sort_records(records)), tuple(_sort_diagnostics(diagnostics))

    def _validate_record(
        self, record: ReviewRecord, repository: str, pr_id: int
    ) -> dict[str, object] | None:
        if (
            record.organization.casefold() == self._org.casefold()
            and record.project.casefold() == self._project.casefold()
            and record.repository.casefold() == repository.casefold()
            and record.pull_request_id == pr_id
        ):
            return None
        return _diagnostic(repository, pr_id, "metadata-scope-mismatch", record.session_id)


def _has_roundtable_label(labels: object) -> bool:
    if not isinstance(labels, list):
        return False
    return any(
        isinstance(item, dict) and parse_label(str(item.get("name") or "")) is not None
        for item in labels
    )


def _metadata_comments(threads: object) -> Iterator[str]:
    if not isinstance(threads, list):
        return
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        for comment in thread.get("comments") or []:
            if not isinstance(comment, dict):
                continue
            content = str(comment.get("content") or "")
            if "Roundtable-Metadata:v1:" in content:
                yield content


def _within_bounds(record: ReviewRecord, lower: datetime | None, upper: datetime | None) -> bool:
    recorded = _recorded_at(record)
    return not (
        (lower is not None and recorded < lower) or (upper is not None and recorded > upper)
    )


def _build_page(inspections: tuple[_Inspection, ...], seen_sessions: set[str]) -> _CollectionPage:
    records: list[ReviewRecord] = []
    diagnostics: list[dict[str, object]] = []
    for inspection in inspections:
        diagnostics.extend(inspection.diagnostics)
        for record in inspection.records:
            if record.session_id not in seen_sessions:
                seen_sessions.add(record.session_id)
                records.append(record)
    return _CollectionPage(
        records=tuple(records),
        diagnostics=tuple(diagnostics),
        prs=len(inspections),
        labels=len(inspections),
        matched=sum(item.matched for item in inspections),
        threads=sum(item.threads for item in inspections),
    )


def _sort_records(records: list[ReviewRecord]) -> list[ReviewRecord]:
    return sorted(
        records,
        key=lambda record: (
            record.recorded_at,
            record.repository.casefold(),
            record.pull_request_id,
            record.session_id,
        ),
    )


def _sort_diagnostics(diagnostics: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        diagnostics,
        key=lambda item: (
            str(item["repository"]).casefold(),
            int(str(item["pullRequestId"])),
            str(item["diagnostic"]),
        ),
    )


def _diagnostic(repository: str, pr_id: int, code: str, detail: str) -> dict[str, object]:
    return {
        "diagnostic": code,
        "repository": repository,
        "pullRequestId": pr_id,
        "detail": detail,
    }


def _record_json(record: ReviewRecord) -> str:
    return json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def write_collection(result: CollectionResult, output: str | Path | None) -> None:
    rendered = result.jsonl()
    for diagnostic in result.diagnostics:
        _write_diagnostic(diagnostic)
    if output is None:
        print(rendered, end="")
        return
    Path(output).write_text(rendered, encoding="utf-8")


def collect_to(
    collector: AzureDevOpsAdoptionCollector,
    output: str | Path | None,
    *,
    repository: str | None = None,
    pull_request_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    quiet: bool = False,
) -> CollectionCounts:
    started_at = _utc_now()
    started = monotonic()
    counts = CollectionCounts()
    target = Path(output) if output is not None else None
    partial = Path(f"{target}.partial") if target is not None else None
    try:
        if partial is None:
            _stream_collection(
                collector,
                sys.stdout,
                counts,
                repository,
                pull_request_id,
                since,
                until,
                quiet,
                started,
            )
        elif target is not None:
            with partial.open("w", encoding="utf-8", newline="\n") as stream:
                _stream_collection(
                    collector,
                    stream,
                    counts,
                    repository,
                    pull_request_id,
                    since,
                    until,
                    quiet,
                    started,
                )
            partial.replace(target)
    except (Exception, KeyboardInterrupt) as err:
        if target is not None:
            status = "interrupted" if isinstance(err, KeyboardInterrupt) else "failed"
            _write_audit(
                target,
                status,
                collector,
                repository,
                pull_request_id,
                started_at,
                started,
                counts,
                err,
            )
        raise
    if target is not None:
        _write_audit(
            target,
            "complete",
            collector,
            repository,
            pull_request_id,
            started_at,
            started,
            counts,
        )
    return counts


def _stream_collection(
    collector: AzureDevOpsAdoptionCollector,
    stream: TextIO,
    counts: CollectionCounts,
    repository: str | None,
    pull_request_id: int | None,
    since: str | None,
    until: str | None,
    quiet: bool,
    started: float,
) -> None:
    for page in collector.pages(
        repository=repository,
        pull_request_id=pull_request_id,
        since=since,
        until=until,
    ):
        for record in page.records:
            stream.write(_record_json(record) + "\n")
        stream.flush()
        for diagnostic in page.diagnostics:
            _write_diagnostic(diagnostic)
        counts.add(page)
        if not quiet:
            _write_progress(counts, started)


def _write_diagnostic(diagnostic: dict[str, object]) -> None:
    print(
        "[adoption collect] diagnostic: "
        + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        file=sys.stderr,
    )


def _write_progress(counts: CollectionCounts, started: float) -> None:
    print(
        "[adoption collect] "
        f"prs={counts.prs} labels={counts.labels} matched={counts.matched} "
        f"threads={counts.threads} records={counts.records} "
        f"elapsed={int(monotonic() - started)}s",
        file=sys.stderr,
    )


def _write_audit(
    target: Path,
    status: str,
    collector: AzureDevOpsAdoptionCollector,
    repository: str | None,
    pull_request_id: int | None,
    started_at: str,
    started: float,
    counts: CollectionCounts,
    error: BaseException | None = None,
) -> None:
    audit: dict[str, object] = {
        "status": status,
        "request": {
            "organization": collector._org,
            "project": collector._project,
            "repository": repository,
            "pullRequestId": pull_request_id,
        },
        "startedAt": started_at,
        "endedAt": _utc_now(),
        "elapsedSeconds": round(monotonic() - started, 3),
        "counts": counts.to_dict(),
    }
    if error is not None:
        audit["error"] = _bounded_error(error)
    Path(f"{target}.audit.json").write_text(
        json.dumps(audit, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _bounded_error(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return (message or type(error).__name__)[:_ERROR_LIMIT]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "AzureDevOpsAdoptionCollector",
    "CollectionCounts",
    "Transport",
    "UrllibTransport",
    "adoption_record_url",
    "collect_to",
]

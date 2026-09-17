"""Stable contracts for Roundtable adoption labels and review metadata."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1
MAX_LABEL_LENGTH = 100
LABEL_PREFIX = "Roundtable-v1-"
METADATA_PREFIX = "<!-- Roundtable-Metadata:v1:"
_METADATA_RE = re.compile(r"<!-- Roundtable-Metadata:v1:([A-Za-z0-9_-]+) -->")
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_PEP440_RE = re.compile(
    r"""(?ix)^
    v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*
    (?:(?:a|b|rc|alpha|beta|pre|preview)[._-]?[0-9]*)?
    (?:(?:-[0-9]+)|(?:[._-]?(?:post|rev|r)[._-]?[0-9]*))?
    (?:[._-]?(?:dev)[._-]?[0-9]*)?
    (?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?
    $"""
)
_CANONICAL_CURRENT_LABEL_RE = re.compile(
    r"""(?ix)^
    (?P<version>
      v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*
      (?:(?:a|b|rc)[0-9]+)?
      (?:\.post[0-9]+)?
      (?:\.dev[0-9]*)?
      (?:\+[a-z0-9]+(?:\.[a-z0-9]+)*)?
    )
    -(?P<configuration>[a-z0-9]+(?:-[a-z0-9]+)*)
    -(?P<source>feed|local)
    $"""
)


@dataclass(frozen=True)
class FindingRecord:
    id: str
    severity: str
    category: str
    agents: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "category": self.category,
            "agents": sorted(set(self.agents)),
        }


@dataclass(frozen=True)
class AdoptionRecord:
    session_id: str
    recorded_at: str
    organization: str
    project: str
    repository: str
    pull_request_id: int
    source_sha: str
    base_sha: str
    tool_version: str
    configuration_name: str
    configuration_kind: str
    graph_config_sha: str
    installation_source: str
    verdict: str
    findings_available: bool
    counts: tuple[tuple[str, int], ...]
    findings: tuple[FindingRecord, ...]
    schema_version: int = SCHEMA_VERSION
    provider: str = "azure_devops"
    canonical_url: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "sessionId": self.session_id,
            "recordedAt": self.recorded_at,
            "organization": self.organization,
            "project": self.project,
            "repository": self.repository,
            "pullRequestId": self.pull_request_id,
            "sourceSha": self.source_sha,
            "baseSha": self.base_sha,
            "toolVersion": self.tool_version,
            "configurationName": self.configuration_name,
            "configurationKind": self.configuration_kind,
            "graphConfigSha": self.graph_config_sha,
            "installationSource": self.installation_source,
            "verdict": self.verdict,
            "findingsAvailable": self.findings_available,
            "counts": dict(sorted(self.counts)),
            "findings": [
                finding.to_dict() for finding in sorted(self.findings, key=_finding_sort_key)
            ],
        }

    @classmethod
    def from_dict(cls, payload: object) -> AdoptionRecord:
        if not isinstance(payload, dict):
            raise ValueError("review metadata must be a JSON object")
        required = {
            "schemaVersion",
            "sessionId",
            "recordedAt",
            "organization",
            "project",
            "repository",
            "pullRequestId",
            "sourceSha",
            "baseSha",
            "toolVersion",
            "configurationName",
            "configurationKind",
            "graphConfigSha",
            "installationSource",
            "verdict",
            "findingsAvailable",
            "counts",
            "findings",
        }
        if set(payload) != required:
            raise ValueError("review metadata fields do not match schema version 1")
        if payload["schemaVersion"] != SCHEMA_VERSION:
            raise ValueError(f"unsupported review metadata schema {payload['schemaVersion']!r}")
        string_fields = required - {
            "schemaVersion",
            "pullRequestId",
            "findingsAvailable",
            "counts",
            "findings",
        }
        if any(not isinstance(payload[field], str) for field in string_fields):
            raise ValueError("review metadata string field has an invalid type")
        if not isinstance(payload["pullRequestId"], int) or payload["pullRequestId"] <= 0:
            raise ValueError("pullRequestId must be a positive integer")
        if payload["installationSource"] not in {"feed", "local"}:
            raise ValueError("installationSource must be feed or local")
        if payload["configurationKind"] not in {"shipped", "external"}:
            raise ValueError("configurationKind must be shipped or external")
        if not isinstance(payload["findingsAvailable"], bool):
            raise ValueError("findingsAvailable must be boolean")
        counts = payload["counts"]
        if not isinstance(counts, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in counts.items()
        ):
            raise ValueError("counts must map strings to non-negative integers")
        findings_payload = payload["findings"]
        if not isinstance(findings_payload, list):
            raise ValueError("findings must be an array")
        findings = tuple(_finding_from_dict(item) for item in findings_payload)
        if not payload["findingsAvailable"] and findings:
            raise ValueError("unavailable findings projection must have an empty findings array")
        return cls(
            session_id=payload["sessionId"],
            recorded_at=payload["recordedAt"],
            organization=payload["organization"],
            project=payload["project"],
            repository=payload["repository"],
            pull_request_id=payload["pullRequestId"],
            source_sha=payload["sourceSha"],
            base_sha=payload["baseSha"],
            tool_version=payload["toolVersion"],
            configuration_name=payload["configurationName"],
            configuration_kind=payload["configurationKind"],
            graph_config_sha=payload["graphConfigSha"],
            installation_source=payload["installationSource"],
            verdict=payload["verdict"],
            findings_available=payload["findingsAvailable"],
            counts=tuple(sorted(counts.items())),
            findings=tuple(sorted(findings, key=_finding_sort_key)),
        )


ReviewRecord = AdoptionRecord


@dataclass(frozen=True)
class AdoptionLabel:
    name: str
    version: str
    configuration: str
    installation_source: str


def _finding_from_dict(payload: object) -> FindingRecord:
    if not isinstance(payload, dict) or set(payload) != {
        "id",
        "severity",
        "category",
        "agents",
    }:
        raise ValueError("finding metadata fields are invalid")
    if any(not isinstance(payload[field], str) for field in ("id", "severity", "category")):
        raise ValueError("finding metadata string field has an invalid type")
    agents = payload["agents"]
    if not isinstance(agents, list) or any(not isinstance(agent, str) for agent in agents):
        raise ValueError("finding agents must be an array of strings")
    return FindingRecord(
        id=payload["id"],
        severity=payload["severity"],
        category=payload["category"],
        agents=tuple(sorted(set(agents))),
    )


def _finding_sort_key(finding: FindingRecord) -> tuple[str, str, str, tuple[str, ...]]:
    return finding.id, finding.severity, finding.category, finding.agents


def canonical_record(record: AdoptionRecord) -> bytes:
    return json.dumps(
        record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def encode_metadata_marker(record: AdoptionRecord) -> str:
    token = base64.urlsafe_b64encode(canonical_record(record)).decode("ascii").rstrip("=")
    return f"{METADATA_PREFIX}{token} -->"


def parse_metadata_markers(text: str) -> list[AdoptionRecord]:
    records: list[AdoptionRecord] = []
    for match in _METADATA_RE.finditer(text or ""):
        token = match.group(1)
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            payload = json.loads(raw.decode("utf-8"))
            record = ReviewRecord.from_dict(payload)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as err:
            raise ValueError(f"invalid Roundtable metadata marker: {err}") from err
        if encode_metadata_marker(record) != match.group(0):
            raise ValueError("Roundtable metadata marker is not canonical")
        records.append(record)
    if "Roundtable-Metadata:" in (text or "") and not records:
        raise ValueError("invalid Roundtable metadata marker syntax")
    return records


def normalize_configuration_name(name: str) -> str:
    token = _TOKEN_RE.sub("-", (name or "").strip().lower()).strip("-")
    return token or "config"


def is_pep440_version(version: str) -> bool:
    return _PEP440_RE.fullmatch((version or "").strip()) is not None


def encode_label(
    version: str,
    configuration_name: str,
    installation_source: str,
    *,
    graph_config_sha: str,
) -> str:
    clean_version = (version or "").strip()
    if not is_pep440_version(clean_version):
        raise ValueError(f"version is not PEP 440: {version!r}")
    if installation_source not in {"feed", "local"}:
        raise ValueError("installation source must be feed or local")
    config = normalize_configuration_name(configuration_name)
    suffix = f"-{installation_source}"
    available = MAX_LABEL_LENGTH - len(LABEL_PREFIX) - len(clean_version) - len(suffix) - 1
    if available < 1:
        raise ValueError("version leaves no room for a configuration label")
    if len(config) > available:
        fingerprint = re.sub(r"[^a-fA-F0-9]", "", graph_config_sha)[:12].lower()
        if not fingerprint:
            fingerprint = hashlib.sha256(graph_config_sha.encode("utf-8")).hexdigest()[:12]
        readable = config[: max(1, available - len(fingerprint) - 1)].rstrip("-")
        config = f"{readable}-{fingerprint}"
    return f"{LABEL_PREFIX}{clean_version}-{config}{suffix}"


def parse_label(name: str) -> AdoptionLabel | None:
    value = (name or "").strip()
    if value.startswith(LABEL_PREFIX):
        body = value[len(LABEL_PREFIX) :]
        current = _CANONICAL_CURRENT_LABEL_RE.fullmatch(body)
        if current is None:
            return None
        return AdoptionLabel(
            value,
            current.group("version"),
            current.group("configuration"),
            current.group("source"),
        )
    return None


def compact_summary(record: AdoptionRecord) -> str:
    finding_text = (
        f"{len(record.findings)} findings"
        if record.findings_available
        else "finding projection unavailable"
    )
    return (
        "### Roundtable review recorded\n"
        f"{record.verdict} · {finding_text} · {record.configuration_name} · "
        f"Roundtable {record.tool_version}"
    )


def finding_records(findings: object) -> tuple[FindingRecord, ...]:
    if not isinstance(findings, list):
        return ()
    normalized = [
        FindingRecord(
            id=str(getattr(finding, "id", "")),
            severity=str(getattr(finding, "severity", "")),
            category=str(getattr(finding, "category", "")),
            agents=tuple(sorted(set(getattr(finding, "source_agents", ()) or ()))),
        )
        for finding in findings
    ]
    return tuple(sorted(normalized, key=_finding_sort_key))

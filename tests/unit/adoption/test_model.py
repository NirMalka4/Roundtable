from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtable.adoption import (
    MAX_LABEL_LENGTH,
    FindingRecord,
    ReviewRecord,
    classify_installation_source,
    encode_label,
    encode_metadata_marker,
    parse_label,
    parse_metadata_markers,
)


def _record() -> ReviewRecord:
    return ReviewRecord(
        session_id="session-1",
        recorded_at="2026-09-09T00:00:00Z",
        organization="contoso",
        project="ExampleProject",
        repository="Repo",
        pull_request_id=42,
        source_sha="s" * 40,
        base_sha="b" * 40,
        tool_version="4.6.3+dev",
        configuration_name="Buddies",
        configuration_kind="shipped",
        graph_config_sha="abcdef1234567890",
        installation_source="local",
        verdict="APPROVE_WITH_SUGGESTIONS",
        findings_available=True,
        counts=(("all", 1),),
        findings=(FindingRecord("RG-1", "medium", "non_blocking", ("api", "redgreen")),),
    )


@pytest.mark.parametrize("version", ["4.6.3", "4.6.3+dev", "4.7.0rc1", "1!4.6.3.post1"])
def test_current_label_round_trips_pep440_versions(version: str) -> None:
    label = encode_label(version, "Inspector X", "feed", graph_config_sha="abcdef")

    parsed = parse_label(label)

    assert parsed is not None
    assert (parsed.version, parsed.configuration, parsed.installation_source) == (
        version,
        "inspector-x",
        "feed",
    )


def test_label_truncates_external_name_with_fingerprint_suffix() -> None:
    label = encode_label(
        "4.6.3",
        "An External Configuration " * 20,
        "local",
        graph_config_sha="0123456789abcdef",
    )

    assert len(label) == MAX_LABEL_LENGTH
    assert "-0123456789ab-local" in label
    assert parse_label(label) is not None


@pytest.mark.parametrize("label", ["Roundtable-4.6.3", "InspectorX-CLI-4.6.3"])
def test_pre_v1_labels_are_not_recognized(label: str) -> None:
    assert parse_label(label) is None


def test_metadata_marker_is_canonical_and_round_trips() -> None:
    marker = encode_metadata_marker(_record())

    assert "+" not in marker and "/" not in marker
    assert parse_metadata_markers(marker) == [_record()]
    payload = parse_metadata_markers(marker)[0].to_dict()
    assert payload["findings"][0]["agents"] == ["api", "redgreen"]


@pytest.mark.parametrize(
    "marker",
    [
        "<!-- Roundtable-Metadata:v1:not-json -->",
        "<!-- Roundtable-Metadata:v1:e30 -->",
        "<!-- Roundtable-Metadata:v1:abc+123 -->",
    ],
)
def test_malformed_or_noncanonical_metadata_is_rejected(marker: str) -> None:
    with pytest.raises(ValueError, match="metadata"):
        parse_metadata_markers(marker)


class _Distribution:
    def __init__(self, direct_url: object = None) -> None:
        self.direct_url = direct_url

    def read_text(self, _filename: str) -> str | None:
        if self.direct_url is None:
            return None
        return json.dumps(self.direct_url)


@pytest.mark.parametrize(
    ("distribution", "package", "roots", "expected"),
    [
        (
            _Distribution(),
            Path("C:/venv/Lib/site-packages/roundtable"),
            ["C:/venv/Lib/site-packages"],
            "feed",
        ),
        (
            _Distribution({"url": "file:///C:/src/roundtable", "dir_info": {}}),
            Path("C:/venv/Lib/site-packages/roundtable"),
            ["C:/venv/Lib/site-packages"],
            "local",
        ),
        (
            _Distribution({"url": "file:///C:/tmp/roundtable.whl", "archive_info": {}}),
            Path("C:/venv/Lib/site-packages/roundtable"),
            ["C:/venv/Lib/site-packages"],
            "local",
        ),
        (
            _Distribution({"url": "https://example/repo.git", "vcs_info": {"vcs": "git"}}),
            Path("C:/venv/Lib/site-packages/roundtable"),
            ["C:/venv/Lib/site-packages"],
            "local",
        ),
        (
            _Distribution({"url": "file:///C:/src", "dir_info": {"editable": True}}),
            Path("C:/venv/Lib/site-packages/roundtable"),
            ["C:/venv/Lib/site-packages"],
            "local",
        ),
        (None, Path("C:/src/roundtable"), ["C:/venv/Lib/site-packages"], "local"),
    ],
)
def test_acquisition_source_uses_distribution_and_source_location(
    distribution, package: Path, roots: list[str], expected: str
) -> None:
    assert classify_installation_source(distribution, package, roots) == expected

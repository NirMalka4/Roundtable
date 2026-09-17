from __future__ import annotations

import json

from roundtable.adoption import FindingRecord, ReviewRecord
from roundtable.adoption.query import query_jsonl


def test_query_aggregates_reviews_and_findings_and_skips_diagnostics(tmp_path) -> None:
    record = ReviewRecord(
        session_id="s1",
        recorded_at="2026-09-09T00:00:00Z",
        organization="o",
        project="p",
        repository="Repo",
        pull_request_id=1,
        source_sha="s",
        base_sha="b",
        tool_version="1.0.0",
        configuration_name="buddies",
        configuration_kind="shipped",
        graph_config_sha="abc",
        installation_source="feed",
        verdict="APPROVE",
        findings_available=True,
        counts=(),
        findings=(FindingRecord("F1", "medium", "non_blocking", ("api", "redgreen")),),
    )
    source = tmp_path / "records.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(record.to_dict()),
                json.dumps({**record.to_dict(), "sessionId": "s2"}),
                json.dumps({"diagnostic": "metadata-missing"}),
            ]
        ),
        encoding="utf-8",
    )

    prs = query_jsonl(source, "prs", "repository")
    reviews = query_jsonl(source, "reviews", "installationSource")
    agents = query_jsonl(source, "findings", "agent")
    categories = query_jsonl(source, "findings", "category")

    assert prs["groups"] == [{"key": "Repo", "value": 1}]
    assert reviews["groups"] == [{"key": "feed", "value": 2}]
    assert agents["groups"] == [
        {"key": "api", "value": 2},
        {"key": "redgreen", "value": 2},
    ]
    assert categories["groups"] == [{"key": "non_blocking", "value": 2}]

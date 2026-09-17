from __future__ import annotations

import json
from dataclasses import replace

from roundtable.adoption import FindingRecord, ReviewRecord, write_report


def _record(session_id: str, pr_id: int) -> ReviewRecord:
    return ReviewRecord(
        session_id=session_id,
        recorded_at=f"2026-09-0{pr_id}T00:00:00Z",
        organization="contoso",
        project="ExampleProject",
        repository="Repo",
        pull_request_id=pr_id,
        source_sha="s",
        base_sha="b",
        tool_version="4.6.3",
        configuration_name="buddies",
        configuration_kind="shipped",
        graph_config_sha="abc",
        installation_source="feed",
        verdict="APPROVE",
        findings_available=True,
        counts=(),
        findings=(FindingRecord(f"F-{pr_id}", "medium", "non_blocking", ("redgreen",)),),
    )


def test_report_renders_offline_dashboard_from_jsonl(tmp_path) -> None:
    source = tmp_path / "reviews.jsonl"
    output = tmp_path / "reports" / "adoption.html"
    source.write_text(
        "\n".join(json.dumps(_record(f"s-{pr}", pr).to_dict()) for pr in (1, 2)),
        encoding="utf-8",
    )

    written = write_report(source, output)
    rendered = written.read_text(encoding="utf-8")

    assert written == output
    assert "<title>Roundtable adoption report</title>" in rendered
    assert "Reviewed PRs</span><strong>2</strong>" in rendered
    assert "Review executions</span><strong>2</strong>" in rendered
    assert "Findings</span><strong>2</strong>" in rendered
    assert "Reviews by acquisition source" in rendered
    assert "Findings by agent" in rendered
    assert "Review execution audit queue" in rendered
    assert "Highest severity" in rendered
    assert "Finding count" not in rendered
    assert "<th>Agents</th>" not in rendered
    assert "<th>Findings</th>" not in rendered
    assert "<select" not in rendered
    assert "Roundtable-Metadata" not in rendered


def test_audit_queue_orders_actionable_reviews_by_severity_then_recency(tmp_path) -> None:
    records = (
        replace(
            _record("approved-critical", 1),
            recorded_at="2026-09-09T00:00:00Z",
            findings=(FindingRecord("critical", "critical", "correctness", ("a",)),),
        ),
        replace(
            _record("rejected-low", 2),
            recorded_at="2026-09-06T00:00:00Z",
            verdict="REJECT",
            findings=(FindingRecord("low", "low", "correctness", ("a",)),),
        ),
        replace(
            _record("suggestions-high-old", 3),
            recorded_at="2026-09-07T00:00:00Z",
            verdict="APPROVE_WITH_SUGGESTIONS",
            findings=(FindingRecord("high-old", "high", "correctness", ("a",)),),
        ),
        replace(
            _record("unknown-high-new", 4),
            recorded_at="2026-09-08T00:00:00Z",
            verdict="UNKNOWN",
            findings=(FindingRecord("high-new", "high", "correctness", ("a",)),),
        ),
    )
    source = tmp_path / "reviews.jsonl"
    output = tmp_path / "adoption.html"
    source.write_text(
        "\n".join(json.dumps(record.to_dict()) for record in records),
        encoding="utf-8",
    )

    rendered = write_report(source, output).read_text(encoding="utf-8")

    positions = [rendered.find(f"pullrequest/{pr_id}") for pr_id in (4, 3, 2, 1)]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)


def test_audit_queue_links_prs_and_exposes_only_summary_fields(tmp_path) -> None:
    record = replace(
        _record("Roundtable-Metadata:private", 7),
        organization="acme org",
        project="A/B",
        repository="Repo & More",
        source_sha="description-secret",
        base_sha="evidence-secret",
        graph_config_sha="remediation-secret",
        configuration_name="<b>external</b>",
        tool_version="5.0&beta",
        verdict="REJECT",
        findings=(
            FindingRecord(
                "F-<7>",
                "high",
                "<unsafe>",
                ("agent&one", "agent<two>"),
            ),
        ),
    )
    source = tmp_path / "reviews.jsonl"
    output = tmp_path / "adoption.html"
    source.write_text(json.dumps(record.to_dict()), encoding="utf-8")

    rendered = write_report(source, output).read_text(encoding="utf-8")

    assert (
        'href="https://dev.azure.com/acme%20org/A%2FB/_git/Repo%20%26%20More/pullrequest/7"'
    ) in rendered
    assert "&lt;b&gt;external&lt;/b&gt;" in rendered
    assert "5.0&amp;beta" in rendered
    assert "Highest severity" in rendered
    assert "2026-09-07T00:00:00Z" in rendered
    assert all(
        secret not in rendered
        for secret in (
            "Roundtable-Metadata",
            "description-secret",
            "evidence-secret",
            "remediation-secret",
        )
    )
    audit_queue = rendered.split('<section class="panel wide">', 1)[1]
    assert "F-&lt;7&gt;" not in audit_queue
    assert "&lt;unsafe&gt;" not in audit_queue
    assert "agent&amp;one" not in audit_queue
    assert "agent&lt;two&gt;" not in audit_queue

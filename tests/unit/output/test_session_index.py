"""Unit tests for output.session_index — the per-repo derived session index."""

from __future__ import annotations

import json
from pathlib import Path

from roundtable.records.session_index import build_repo_index, write_repo_index


def _session(repo_dir: Path, name: str, trace: dict) -> None:
    d = repo_dir / name
    d.mkdir(parents=True)
    (d / "trace.json").write_text(json.dumps(trace), encoding="utf-8")


def test_index_is_ordered_chronologically_by_session_dir(tmp_path: Path) -> None:
    # Two reviews of the SAME branch differ by the timestamp in the session id and
    # by sourceSha — the index must surface both, in chronological order.
    _session(
        tmp_path,
        "session_20260713010000_feat-x",
        {
            "sessionId": "s1",
            "verdict": "APPROVE",
            "subject": {"sourceBranch": "feat/x", "sourceSha": "aaa"},
        },
    )
    _session(
        tmp_path,
        "session_20260713020000_feat-x",
        {
            "sessionId": "s2",
            "verdict": "REJECT",
            "subject": {"sourceBranch": "feat/x", "sourceSha": "bbb"},
        },
    )
    index = build_repo_index(tmp_path)
    dirs = [r["dir"] for r in index["reviews"]]
    assert dirs == ["session_20260713010000_feat-x", "session_20260713020000_feat-x"]
    assert [r["sourceSha"] for r in index["reviews"]] == ["aaa", "bbb"]
    assert index["repo"] == tmp_path.name


def test_index_flattens_subject_and_verdict(tmp_path: Path) -> None:
    _session(
        tmp_path,
        "session_20260713010000_pr-9",
        {
            "sessionId": "s1",
            "verdict": "APPROVE",
            "verdictIcon": "\u2705",
            "agentCount": 28,
            "counts": {"all": 3},
            "subject": {"mode": "pr", "prId": 9, "prTitle": "Fix bug", "sourceBranch": "feat/z"},
        },
    )
    row = build_repo_index(tmp_path)["reviews"][0]
    assert row["mode"] == "pr"
    assert row["prId"] == 9
    assert row["prTitle"] == "Fix bug"
    assert row["counts"] == {"all": 3}
    assert row["agentCount"] == 28


def test_index_skips_dirs_without_or_with_bad_trace(tmp_path: Path) -> None:
    _session(tmp_path, "session_ok", {"sessionId": "ok", "verdict": "APPROVE"})
    (tmp_path / "copilot-home").mkdir()  # non-session sibling, no trace.json
    bad = tmp_path / "session_bad"
    bad.mkdir()
    (bad / "trace.json").write_text("{not json", encoding="utf-8")
    index = build_repo_index(tmp_path)
    assert [r["dir"] for r in index["reviews"]] == ["session_ok"]


def test_index_tolerates_legacy_trace_without_subject(tmp_path: Path) -> None:
    _session(tmp_path, "session_legacy", {"sessionId": "leg", "verdict": "REJECT"})
    row = build_repo_index(tmp_path)["reviews"][0]
    assert row["dir"] == "session_legacy"
    assert row["verdict"] == "REJECT"
    assert "sourceBranch" not in row  # no subject ⇒ minimal row


def test_index_carries_timing_cost_and_base_sha(tmp_path: Path) -> None:
    _session(
        tmp_path,
        "session_20260713010000_feat-x",
        {
            "sessionId": "s1",
            "verdict": "APPROVE",
            "startedAt": "2026-07-13T01:00:00.000000Z",
            "finishedAt": "2026-07-13T01:02:30.000000Z",
            "usage": {
                "outputTokens": 4200,
                "rounds": 30,
                "billing": {
                    "source": "copilot-sdk/session.usage.getMetrics",
                    "status": "complete",
                    "totalNanoAiu": 1_500_000_000.0,
                    "totalPremiumRequestCost": 1.75,
                },
            },
            "subject": {"sourceBranch": "feat/x", "sourceSha": "aaa", "baseSha": "base0"},
        },
    )
    row = build_repo_index(tmp_path)["reviews"][0]
    assert row["startedAt"] == "2026-07-13T01:00:00.000000Z"
    assert row["durationSeconds"] == 150.0
    assert row["outputTokens"] == 4200
    assert row["billing"]["totalNanoAiu"] == 1_500_000_000.0
    assert row["billing"]["totalPremiumRequestCost"] == 1.75
    assert row["baseSha"] == "base0"


def test_index_keeps_legacy_premium_requests_as_machine_telemetry(tmp_path: Path) -> None:
    _session(
        tmp_path,
        "session_legacy",
        {
            "sessionId": "legacy",
            "usage": {"premiumRequests": 1.5},
        },
    )
    row = build_repo_index(tmp_path)["reviews"][0]
    assert row["premiumRequests"] == 1.5
    assert "billing" not in row


def test_index_omits_duration_and_cost_when_data_missing(tmp_path: Path) -> None:
    # No timestamps ⇒ no duration; no usage ⇒ no cost columns (minimal, valid row).
    _session(tmp_path, "session_bare", {"sessionId": "b", "verdict": "APPROVE"})
    row = build_repo_index(tmp_path)["reviews"][0]
    assert "durationSeconds" not in row
    assert "outputTokens" not in row
    assert "billing" not in row
    assert "premiumRequests" not in row


def test_write_repo_index_is_rebuildable(tmp_path: Path) -> None:
    _session(tmp_path, "session_a", {"sessionId": "a", "verdict": "APPROVE"})
    p1 = write_repo_index(tmp_path)
    first = p1.read_text(encoding="utf-8")
    # Deleting and regenerating yields byte-identical output (fully derived).
    p1.unlink()
    p2 = write_repo_index(tmp_path)
    assert p2.read_text(encoding="utf-8") == first
    assert not (tmp_path / "index.json.tmp").exists()

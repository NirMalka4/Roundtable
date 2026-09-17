from __future__ import annotations

import json
import threading

import pytest

from roundtable.adoption import ReviewRecord, collector, encode_metadata_marker
from roundtable.adoption.collector import AdoptionCollector, collect_to


def _record(
    session: str = "session-1",
    *,
    pr_id: int = 42,
    recorded_at: str = "2026-09-09T00:00:00Z",
) -> ReviewRecord:
    return ReviewRecord(
        session_id=session,
        recorded_at=recorded_at,
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
        findings=(),
    )


class _Transport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def request(self, method, url, *, headers, body, timeout):
        self.urls.append(url)
        if "/repositories?" in url:
            return 200, json.dumps({"value": [{"id": "repo-id", "name": "Repo"}]})
        if "/pullrequests?" in url:
            return 200, json.dumps(
                {"value": [{"pullRequestId": 42, "creationDate": "2099-01-01T00:00:00Z"}]}
            )
        if "/labels?" in url:
            return 200, json.dumps({"value": [{"name": "Roundtable-v1-4.6.3-buddies-feed"}]})
        if "/threads?" in url:
            return 200, json.dumps(
                {
                    "value": [
                        {
                            "comments": [
                                {"content": encode_metadata_marker(_record("session-2"))},
                                {"content": encode_metadata_marker(_record("session-1"))},
                            ]
                        }
                    ]
                }
            )
        raise AssertionError(url)


def test_collector_prefilters_labels_and_emits_deterministic_jsonl(monkeypatch) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")
    transport = _Transport()

    result = AdoptionCollector(
        "contoso",
        "ExampleProject",
        transport=transport,
        page_size=100,
    ).collect(since="2026-09-01", until="2026-09-30")

    assert [record.session_id for record in result.records] == ["session-1", "session-2"]
    labels_index = next(i for i, url in enumerate(transport.urls) if "/labels?" in url)
    threads_index = next(i for i, url in enumerate(transport.urls) if "/threads?" in url)
    assert labels_index < threads_index
    assert result.jsonl() == result.jsonl()
    assert [json.loads(line)["sessionId"] for line in result.jsonl().splitlines()] == [
        "session-1",
        "session-2",
    ]


class _PagedTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def request(self, method, url, *, headers, body, timeout):
        self.urls.append(url)
        skip = "$skip=1" in url
        if "/repositories?" in url:
            value = [] if skip else [{"id": "repo-id", "name": "Repo"}]
        elif "/pullrequests?" in url:
            value = [] if skip else [{"pullRequestId": 42}]
        elif "/labels?" in url:
            value = [{"name": "Roundtable-v1-4.6.3-buddies-feed"}]
        elif "/threads?" in url:
            value = []
        else:
            raise AssertionError(url)
        return 200, json.dumps({"value": value})


def test_collector_paginates_and_reports_missing_metadata(monkeypatch) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")
    transport = _PagedTransport()

    result = AdoptionCollector(
        "contoso",
        "ExampleProject",
        transport=transport,
        page_size=1,
    ).collect()

    assert result.records == ()
    assert result.diagnostics[0]["diagnostic"] == "metadata-missing"
    assert sum("$skip=1" in url for url in transport.urls) == 2


class _DirectTransport:
    def __init__(self, *, matched: bool = True) -> None:
        self.urls: list[str] = []
        self.matched = matched

    def request(self, method, url, *, headers, body, timeout):
        self.urls.append(url)
        if "/repositories?" in url:
            return 200, json.dumps({"value": [{"id": "repo-id", "name": "Repo"}]})
        if "/repositories/Repo?" in url:
            return 200, json.dumps({"id": "repo-id", "name": "Repo"})
        if "/pullrequests/42?" in url:
            return 200, json.dumps({"pullRequestId": 42})
        if "/labels?" in url:
            labels = [{"name": "Roundtable-v1-4.6.3-buddies-feed"}] if self.matched else []
            return 200, json.dumps({"value": labels})
        if "/threads?" in url:
            marker = encode_metadata_marker(_record())
            return 200, json.dumps({"value": [{"comments": [{"content": marker}]}]})
        raise AssertionError(url)


def test_direct_pr_resolves_repository_and_pr_without_listing_history(monkeypatch) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")
    transport = _DirectTransport()

    result = AdoptionCollector("contoso", "ExampleProject", transport=transport).collect(
        repository="Repo",
        pull_request_id=42,
    )

    assert [record.session_id for record in result.records] == ["session-1"]
    assert any("/repositories/Repo?" in url for url in transport.urls)
    assert any("/pullrequests/42?" in url for url in transport.urls)
    assert not any("/repositories?" in url for url in transport.urls)
    assert not any("/pullrequests?" in url for url in transport.urls)


def test_unmatched_label_does_not_fetch_threads(monkeypatch) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")
    transport = _DirectTransport(matched=False)

    result = AdoptionCollector("contoso", "ExampleProject", transport=transport).collect(
        repository="Repo",
        pull_request_id=42,
    )

    assert result.records == ()
    assert any("/labels?" in url for url in transport.urls)
    assert not any("/threads?" in url for url in transport.urls)


class _ConcurrentPagedTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.barrier = threading.Barrier(8)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.first_page_finished: set[int] = set()

    def request(self, method, url, *, headers, body, timeout):
        self.urls.append(url)
        if "/repositories/Repo?" in url:
            return 200, json.dumps({"id": "repo-id", "name": "Repo"})
        if "/pullrequests?" in url:
            return self._pull_requests(url)
        pr_id = int(url.split("/pullrequests/", 1)[1].split("/", 1)[0])
        if "/labels?" in url:
            self._enter_label_request(pr_id)
            return 200, json.dumps({"value": [{"name": "Roundtable-v1-4.6.3-buddies-feed"}]})
        if "/threads?" in url:
            if pr_id <= 8:
                with self.lock:
                    self.first_page_finished.add(pr_id)
            marker = encode_metadata_marker(_record(f"session-{pr_id}", pr_id=pr_id))
            return 200, json.dumps({"value": [{"comments": [{"content": marker}]}]})
        raise AssertionError(url)

    def _pull_requests(self, url):
        if "$skip=0" in url:
            return 200, json.dumps(
                {"value": [{"pullRequestId": pr_id} for pr_id in range(8, 0, -1)]}
            )
        if "$skip=8" in url:
            assert self.first_page_finished == set(range(1, 9))
            return 200, json.dumps({"value": [{"pullRequestId": 10}, {"pullRequestId": 9}]})
        raise AssertionError(url)

    def _enter_label_request(self, pr_id):
        if pr_id > 8:
            return
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.barrier.wait(timeout=5)
        with self.lock:
            self.active -= 1


def test_repository_scan_is_page_bounded_with_eight_ordered_inspections(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")
    transport = _ConcurrentPagedTransport()
    output = tmp_path / "records.jsonl"

    collect_to(
        AdoptionCollector("contoso", "ExampleProject", transport=transport, page_size=8),
        output,
        repository="Repo",
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    progress = capsys.readouterr().err.splitlines()
    assert [row["pullRequestId"] for row in rows] == list(range(1, 11))
    assert transport.max_active == 8
    assert len(progress) == 2
    assert progress[0].startswith(
        "[adoption collect] prs=8 labels=8 matched=8 threads=8 records=8 elapsed="
    )
    assert progress[1].startswith(
        "[adoption collect] prs=10 labels=10 matched=10 threads=10 records=10 elapsed="
    )


class _RepeatedPageTransport:
    def request(self, method, url, *, headers, body, timeout):
        if "/repositories/Repo?" in url:
            return 200, json.dumps({"id": "repo-id", "name": "Repo"})
        if "/pullrequests?" in url:
            return 200, json.dumps({"value": [{"pullRequestId": 1}]})
        if "/labels?" in url:
            return 200, json.dumps({"value": []})
        raise AssertionError(url)


def test_repeated_full_page_fails_instead_of_looping(monkeypatch) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")

    with pytest.raises(RuntimeError, match="repeated a full page"):
        AdoptionCollector(
            "contoso",
            "ExampleProject",
            transport=_RepeatedPageTransport(),
            page_size=1,
        ).collect(repository="Repo")


def test_collect_to_atomically_replaces_output_and_writes_audit(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")
    output = tmp_path / "records.jsonl"
    output.write_text("old output\n", encoding="utf-8")

    collect_to(
        AdoptionCollector("contoso", "ExampleProject", transport=_DirectTransport()),
        output,
        repository="Repo",
        pull_request_id=42,
    )

    row = json.loads(output.read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "records.jsonl.audit.json").read_text(encoding="utf-8"))
    assert row["sessionId"] == "session-1"
    assert not (tmp_path / "records.jsonl.partial").exists()
    assert audit["status"] == "complete"
    assert audit["request"] == {
        "organization": "contoso",
        "project": "ExampleProject",
        "repository": "Repo",
        "pullRequestId": 42,
    }
    assert audit["counts"] == {
        "prs": 1,
        "labels": 1,
        "matched": 1,
        "threads": 1,
        "records": 1,
        "diagnostics": 0,
    }
    assert {"startedAt", "endedAt", "elapsedSeconds"} <= audit.keys()
    assert capsys.readouterr().err.startswith(
        "[adoption collect] prs=1 labels=1 matched=1 threads=1 records=1 elapsed="
    )


def test_quiet_streams_jsonl_without_progress(monkeypatch, capsys) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")

    collect_to(
        AdoptionCollector("contoso", "ExampleProject", transport=_DirectTransport()),
        None,
        repository="Repo",
        pull_request_id=42,
        quiet=True,
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out)["sessionId"] == "session-1"
    assert captured.err == ""


class _FailAfterPageTransport(_DirectTransport):
    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def request(self, method, url, *, headers, body, timeout):
        if "/pullrequests?" in url:
            if "$skip=0" in url:
                return 200, json.dumps({"value": [{"pullRequestId": 42}]})
            raise self.error
        return super().request(method, url, headers=headers, body=body, timeout=timeout)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RuntimeError("failed\n" + "x" * 400), "failed"),
        (KeyboardInterrupt(), "interrupted"),
    ],
)
def test_failed_collection_retains_partial_old_output_and_bounded_audit(
    monkeypatch, tmp_path, error, status
) -> None:
    monkeypatch.setattr(collector, "ado_auth_header", lambda: "Basic test")
    output = tmp_path / "records.jsonl"
    output.write_text("old output\n", encoding="utf-8")

    with pytest.raises(type(error)):
        collect_to(
            AdoptionCollector(
                "contoso",
                "ExampleProject",
                transport=_FailAfterPageTransport(error),
                page_size=1,
            ),
            output,
            repository="Repo",
            quiet=True,
        )

    partial = json.loads((tmp_path / "records.jsonl.partial").read_text(encoding="utf-8"))
    audit = json.loads((tmp_path / "records.jsonl.audit.json").read_text(encoding="utf-8"))
    assert output.read_text(encoding="utf-8") == "old output\n"
    assert partial["sessionId"] == "session-1"
    assert audit["status"] == status
    assert audit["counts"]["records"] == 1
    assert "\n" not in audit["error"]
    assert len(audit["error"]) <= 300

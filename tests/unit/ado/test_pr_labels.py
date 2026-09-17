"""Unit tests for the one current Roundtable adoption label."""

from __future__ import annotations

import json

import pytest

from roundtable.ado import pr_labels
from roundtable.ado.pr_labels import PrLabelClient, stamp_adoption_label
from roundtable.inputs.pr_reference import PrReference

_LABEL = "Roundtable-v1-4.6.3-buddies-feed"
_STALE = "Roundtable-v1-4.6.2-buddies-feed"


class FakeTransport:
    def __init__(self, labels=None, *, fail_get=False, fail_post=False):
        self.labels: list[dict] = [dict(x) for x in (labels or [])]
        self._fail_get = fail_get
        self._fail_post = fail_post
        self.methods: list[str] = []

    def request(self, method, url, *, headers, body, timeout):
        self.methods.append(method)
        if method == "GET":
            return (500, "") if self._fail_get else (200, json.dumps({"value": self.labels}))
        if method == "POST":
            if self._fail_post:
                return 500, ""
            name = json.loads(body.decode("utf-8"))["name"]
            self.labels.append({"id": f"id-{name}", "name": name})
            return 201, "{}"
        if method == "DELETE":
            ident = url.split("/labels/")[1].split("?")[0]
            self.labels = [
                item
                for item in self.labels
                if str(item.get("id")) != ident and str(item.get("name")) != ident
            ]
            return 204, ""
        return 400, ""


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setattr(pr_labels, "ado_auth_header", lambda: "Basic xxx")


def _pr() -> PrReference:
    return PrReference(org="o", project="p", repo_name="r", pr_id=42, host="dev.azure.com")


def _client(transport) -> PrLabelClient:
    return PrLabelClient(_pr(), transport=transport)


def test_apply_adds_when_absent():
    transport = FakeTransport([])

    assert _client(transport).apply_adoption_label(_LABEL) == "added"
    assert any(item["name"] == _LABEL for item in transport.labels)


def test_apply_present_makes_no_write():
    transport = FakeTransport([{"id": "1", "name": _LABEL}])

    assert _client(transport).apply_adoption_label(_LABEL) == "present"
    assert transport.methods == ["GET"]


def test_apply_replaces_stale_and_leaves_foreign_labels():
    transport = FakeTransport(
        [
            {"id": "1", "name": _STALE},
            {"id": "2", "name": "needs-work"},
            {"id": "3", "name": "Roundtable-4.6.1"},
        ]
    )

    assert _client(transport).apply_adoption_label(_LABEL) == "replaced"
    assert {item["name"] for item in transport.labels} == {
        _LABEL,
        "needs-work",
        "Roundtable-4.6.1",
    }


def test_stamp_rejects_non_v1_label():
    with pytest.raises(ValueError, match="invalid current-state"):
        stamp_adoption_label(_pr(), "Roundtable-4.6.3")


def test_stamp_returns_failed_on_transport_error():
    transport = FakeTransport([], fail_get=True)

    assert stamp_adoption_label(_pr(), _LABEL, client_factory=lambda pr: _client(transport)) == (
        _LABEL,
        "failed",
    )


def test_stamp_added_happy_path():
    transport = FakeTransport([])

    assert stamp_adoption_label(_pr(), _LABEL, client_factory=lambda pr: _client(transport)) == (
        _LABEL,
        "added",
    )

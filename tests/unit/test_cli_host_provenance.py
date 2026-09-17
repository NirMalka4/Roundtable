"""The ``provenance.host`` block — which machine produced a session's artifacts.

A verdict comment points a reader at ``~/roundtable/artifacts/<repo>/<session>``,
a path that exists on the reviewing machine only. Naming that machine is what
makes the pointer actionable, so the name must survive every platform the tool
runs on (Windows, macOS, Ubuntu) and must never come out blank.
"""

from __future__ import annotations

import platform
import socket
from types import SimpleNamespace

import pytest

from roundtable import cli


def _args() -> SimpleNamespace:
    return SimpleNamespace(
        base_branch=None,
        pr=None,
        max_attempts=None,
        session_reuse=True,
        simulate=True,
        hint=None,
        hint_path=None,
    )


def test_provenance_records_the_executing_host() -> None:
    host = cli._provenance(_args())["host"]

    assert host["name"], "a blank host makes the artifacts path in a report unusable"
    assert host["os"] == platform.system()


@pytest.mark.parametrize(
    ("system", "node"),
    [
        ("Windows", "PRIVATE-USER-DEV"),
        ("Darwin", "private-user-mac.local"),
        ("Linux", "ubuntu-ci-01"),
    ],
)
def test_host_is_reported_verbatim_on_every_supported_platform(
    monkeypatch: pytest.MonkeyPatch, system: str, node: str
) -> None:
    """Record what the OS reports — including macOS's ``.local`` mDNS suffix.

    Provenance is evidence, so it is never normalized; a reader comparing two
    sessions must see the names their own machines report.
    """
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "node", lambda: node)

    assert cli._host() == {"name": node, "os": system}


def test_host_name_falls_back_to_socket_when_platform_node_is_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``platform.node()`` is documented to return ``""`` when undeterminable."""
    monkeypatch.setattr(platform, "node", lambda: "")
    monkeypatch.setattr(socket, "gethostname", lambda: "ubuntu-ci-01")

    assert cli._host_name() == "ubuntu-ci-01"


def test_host_name_is_never_blank_when_the_machine_cannot_be_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "node", lambda: "")
    monkeypatch.setattr(socket, "gethostname", lambda: "")

    assert cli._host_name() == cli._UNKNOWN_HOST


def test_a_failing_hostname_lookup_never_breaks_a_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> str:
        raise OSError("no resolver")

    monkeypatch.setattr(platform, "node", lambda: "")
    monkeypatch.setattr(socket, "gethostname", _boom)

    assert cli._host_name() == cli._UNKNOWN_HOST

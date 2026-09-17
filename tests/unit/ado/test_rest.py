"""Unit tests for ado.rest — the shared, dependency-neutral ADO REST surface.

Covers the safety envelope added when the auth/URL helpers were lifted out of
``inputs/pr_diff.py`` (E3/E9): host allow-listing, https base-URL building,
path-segment encoding, and PAT-first auth-header routing. The auth-header cases
mirror the historical ``pr_diff`` tests so behavior is provably identical.
"""

from __future__ import annotations

import pytest

from roundtable.ado_client import client as rest


# ── host allow-list (E3) ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "host, allowed",
    [
        ("dev.azure.com", True),
        ("DEV.AZURE.COM", True),
        ("myorg.visualstudio.com", True),
        ("evil.com", False),
        ("dev.azure.com.evil.com", False),
        ("", False),
    ],
)
def test_is_allowed_ado_host(host: str, allowed: bool) -> None:
    assert rest.is_allowed_ado_host(host) is allowed


def test_ensure_allowed_ado_host_rejects_untrusted() -> None:
    with pytest.raises(RuntimeError, match="untrusted host"):
        rest.ensure_allowed_ado_host("evil.com")


# ── base-URL building (both host forms, https-only) ─────────────────────────
def test_build_ado_base_url_modern_host() -> None:
    assert rest.build_ado_base_url("dev.azure.com", "myorg") == "https://dev.azure.com/myorg"


def test_build_ado_base_url_legacy_vsts_host() -> None:
    assert (
        rest.build_ado_base_url("myorg.visualstudio.com", "myorg")
        == "https://myorg.visualstudio.com"
    )


def test_build_ado_base_url_refuses_untrusted_host() -> None:
    with pytest.raises(RuntimeError, match="untrusted host"):
        rest.build_ado_base_url("evil.com", "myorg")


# ── path-segment encoding ────────────────────────────────────────────────────
def test_encode_segment_escapes_slash_and_space() -> None:
    assert rest.encode_segment("a/b c") == "a%2Fb%20c"


# ── auth header routing (identical to former pr_diff behavior) ───────────────
def test_ado_auth_header_prefers_pat(monkeypatch) -> None:
    monkeypatch.setenv("ROUNDTABLE_ADO_PAT", "secret-pat")
    monkeypatch.setattr(
        rest,
        "ado_bearer_token",
        lambda **_: (_ for _ in ()).throw(AssertionError("az must not be called")),
    )
    header = rest.ado_auth_header()
    assert header is not None and header.startswith("Basic ")


def test_ado_auth_header_falls_back_to_bearer(monkeypatch) -> None:
    monkeypatch.delenv("ROUNDTABLE_ADO_PAT", raising=False)
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    monkeypatch.setattr(rest, "ado_bearer_token", lambda **_: "aad-token-xyz")
    assert rest.ado_auth_header() == "Bearer aad-token-xyz"


def test_ado_auth_header_none_when_no_credentials(monkeypatch) -> None:
    monkeypatch.delenv("ROUNDTABLE_ADO_PAT", raising=False)
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    monkeypatch.setattr(rest, "ado_bearer_token", lambda **_: None)
    assert rest.ado_auth_header() is None


# ── bearer-token decode safety (Windows charmap regression guard) ────────────
def _bad_byte_run(captured: dict):
    """A ``subprocess.run`` stand-in that runs a REAL child emitting an invalid
    byte (``0x8f``), forwarding only the caller's decode kwargs. ``0x8f`` is
    invalid in UTF-8 and undefined in cp1252, so a strict decode raises
    ``UnicodeDecodeError`` on every platform unless ``errors='replace'`` is set —
    reproducing the Windows ``charmap`` crash from the ``az`` token reader.
    """
    import subprocess
    import sys as _sys

    real_run = subprocess.run

    def _run(cmd, **kwargs):
        captured.update(kwargs)
        decode_kw = {
            k: kwargs[k] for k in ("capture_output", "text", "encoding", "errors") if k in kwargs
        }
        return real_run(
            [_sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'tok\x8f\n')"],
            timeout=kwargs.get("timeout"),
            **decode_kw,
        )

    return _run


def test_ado_bearer_token_decodes_non_utf8_output_safely(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(rest.subprocess, "run", _bad_byte_run(captured))
    # az may emit non-UTF-8 bytes; minting must not crash the reader thread.
    token = rest.ado_bearer_token()
    assert token is not None and token.startswith("tok")
    assert captured.get("encoding") == "utf-8" and captured.get("errors") == "replace"

"""Unit tests for the SDK backend auth precedence resolver."""

from __future__ import annotations

import pytest

from roundtable.backend.sdk.auth import (
    AuthConfig,
    AuthMode,
    AuthUnavailableError,
    resolve_auth,
)

_SECRET = "ghp_supersecrettoken1234567890"


def test_explicit_token_wins() -> None:
    cfg = resolve_auth(_SECRET, env={"GITHUB_TOKEN": "env-tok", "GH_TOKEN": "gh-tok"})
    assert cfg.mode is AuthMode.TOKEN
    assert cfg.source == "explicit"
    assert cfg.client_kwargs() == {"github_token": _SECRET, "use_logged_in_user": False}


def test_github_token_precedes_gh_token() -> None:
    cfg = resolve_auth(env={"GITHUB_TOKEN": "primary", "GH_TOKEN": "secondary"})
    assert cfg.source == "env:GITHUB_TOKEN"
    assert cfg.client_kwargs()["github_token"] == "primary"


def test_gh_token_used_when_github_token_absent() -> None:
    cfg = resolve_auth(env={"GH_TOKEN": "secondary"})
    assert cfg.source == "env:GH_TOKEN"


def test_blank_tokens_are_ignored() -> None:
    cfg = resolve_auth("   ", env={"GITHUB_TOKEN": "   "})
    assert cfg.mode is AuthMode.LOGGED_IN_USER


def test_logged_in_user_fallback() -> None:
    cfg = resolve_auth(env={})
    assert cfg.mode is AuthMode.LOGGED_IN_USER
    assert cfg.source == "logged_in_user"
    assert cfg.client_kwargs() == {"use_logged_in_user": True}


def test_headless_without_token_raises() -> None:
    with pytest.raises(AuthUnavailableError) as exc:
        resolve_auth(env={}, allow_logged_in_user=False)
    assert "GITHUB_TOKEN" in str(exc.value)


def test_headless_with_env_token_ok() -> None:
    cfg = resolve_auth(env={"GITHUB_TOKEN": _SECRET}, allow_logged_in_user=False)
    assert cfg.mode is AuthMode.TOKEN


def test_token_never_leaks_in_repr_or_str_or_describe() -> None:
    cfg = resolve_auth(_SECRET)
    for text in (repr(cfg), str(cfg), cfg.describe(), f"{cfg}"):
        assert _SECRET not in text
    assert "<redacted>" in repr(cfg)
    # describe() carries provenance but never the secret.
    assert cfg.describe() == "auth(mode=token, source=explicit)"


def test_dataclass_default_repr_would_leak_but_ours_does_not() -> None:
    # Guard against someone removing the custom __repr__: a plain dataclass repr
    # WOULD contain the token, so this asserts our override is in force.
    cfg = AuthConfig(AuthMode.TOKEN, "explicit", _SECRET)
    assert _SECRET not in repr(cfg)

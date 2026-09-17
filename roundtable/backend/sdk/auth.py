"""Auth precedence resolver for the SDK backend (pure — no SDK import).

The CLI backend inherits the operator's ambient Copilot login. The SDK is
**explicit**: ``CopilotClient(github_token=..., use_logged_in_user=...)``. This
module decides *which* auth to hand the client, from a fixed precedence:

    explicit github_token  >  env GITHUB_TOKEN  >  env GH_TOKEN  >  logged-in user

``use_logged_in_user`` is the interactive/dev default (proved viable under an
isolated home in the spike, m85) but is **not** a CI strategy — headless runs must
provide a token via env. When no token is available and logged-in fallback is
disallowed, resolution fails loudly rather than silently running unauthenticated.

Safety invariant: **a resolved token never appears in any artifact or log.**
:class:`AuthConfig` redacts the token in ``repr``/``str``; use :meth:`describe`
for diagnostics and :meth:`client_kwargs` to feed the SDK client.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

_TOKEN_ENV_VARS = ("GITHUB_TOKEN", "GH_TOKEN")


class AuthMode(StrEnum):
    """How the SDK client should authenticate."""

    TOKEN = "token"
    LOGGED_IN_USER = "logged_in_user"


class AuthUnavailableError(RuntimeError):
    """No token found and logged-in-user fallback was disallowed (e.g. CI)."""


@dataclass(frozen=True)
class AuthConfig:
    """Resolved auth decision. Never serialize ``_token`` — it is secret."""

    mode: AuthMode
    source: str  # non-secret provenance, e.g. "explicit", "env:GITHUB_TOKEN", "logged_in_user"
    _token: str | None = None

    def describe(self) -> str:
        """Non-secret one-liner safe for logs/artifacts (never the token value)."""
        return f"auth(mode={self.mode.value}, source={self.source})"

    def client_kwargs(self) -> dict[str, object]:
        """Kwargs for ``CopilotClient`` — the one place the token leaves this object."""
        if self.mode is AuthMode.TOKEN:
            return {"github_token": self._token, "use_logged_in_user": False}
        return {"use_logged_in_user": True}

    def __repr__(self) -> str:  # redact — dataclass default would print the token
        return f"AuthConfig(mode={self.mode.value!r}, source={self.source!r}, token=<redacted>)"

    __str__ = __repr__


def resolve_auth(
    explicit_token: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    allow_logged_in_user: bool = True,
) -> AuthConfig:
    """Resolve auth by fixed precedence; raise if nothing usable is available.

    ``allow_logged_in_user=False`` (headless/CI) forces a token and turns a
    missing one into :class:`AuthUnavailableError` instead of a silent
    interactive-login attempt.
    """
    env = os.environ if env is None else env

    if explicit_token and explicit_token.strip():
        return AuthConfig(AuthMode.TOKEN, "explicit", explicit_token)

    for var in _TOKEN_ENV_VARS:
        value = env.get(var)
        if value and value.strip():
            return AuthConfig(AuthMode.TOKEN, f"env:{var}", value)

    if allow_logged_in_user:
        return AuthConfig(AuthMode.LOGGED_IN_USER, "logged_in_user")

    raise AuthUnavailableError(
        "No GitHub token found (checked explicit arg, "
        f"{', '.join(_TOKEN_ENV_VARS)}) and logged-in-user fallback is disabled. "
        "Set GITHUB_TOKEN for headless/CI runs."
    )

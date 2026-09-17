"""Duplicate-safe registry for repository providers."""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

from .model import ChangeRequestIdentity, RepositoryIdentity


@runtime_checkable
class RepositoryProvider(Protocol):
    id: str

    def canonicalize_remote(self, remote_url: str) -> str | None: ...

    def parse_repository(self, remote_url: str) -> RepositoryIdentity | None: ...

    def parse_change_request(self, value: str) -> ChangeRequestIdentity | None: ...


_PROVIDERS: dict[str, RepositoryProvider | str] = {
    "azure_devops": "roundtable.ado.provider:AzureDevOpsProvider",
}


def register_provider(name: str, provider: RepositoryProvider | str) -> None:
    if name in _PROVIDERS:
        raise ValueError(f"provider already registered: {name!r}")
    _PROVIDERS[name] = provider


def get_provider(name: str) -> RepositoryProvider:
    try:
        registered = _PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(_PROVIDERS)) or "(none)"
        raise ValueError(f"unknown provider {name!r}; known providers: {known}") from None
    if isinstance(registered, str):
        module, _, attribute = registered.partition(":")
        registered = getattr(importlib.import_module(module), attribute)()
        _PROVIDERS[name] = registered
    return registered


def canonicalize_remote(remote_url: str) -> str | None:
    for name in tuple(_PROVIDERS):
        if canonical := get_provider(name).canonicalize_remote(remote_url):
            return canonical
    return None

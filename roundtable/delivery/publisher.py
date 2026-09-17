"""Destination-neutral publisher contract and registry."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PublicationTarget:
    provider: str
    locator: str | None = None


@dataclass(frozen=True)
class PublishRequest:
    session_dir: Path
    target: PublicationTarget | None = None
    options: object | None = None


@dataclass(frozen=True)
class RetractRequest:
    session_dir: Path
    target: PublicationTarget | None = None
    options: object | None = None


@dataclass(frozen=True)
class PublishOutcome:
    ok: bool
    exit_code: int
    message: str = ""


@runtime_checkable
class Publisher(Protocol):
    name: str

    def publish(self, request: PublishRequest) -> PublishOutcome: ...

    def retract(self, request: RetractRequest) -> PublishOutcome: ...


_PUBLISHERS: dict[str, Publisher | str] = {
    "azure_devops": "roundtable.ado.sink:AzureDevOpsPublisher",
}


def register_publisher(name: str, publisher: Publisher | str) -> None:
    if name in _PUBLISHERS:
        raise ValueError(f"publisher already registered: {name!r}")
    _PUBLISHERS[name] = publisher


def get_publisher(name: str | None = "azure_devops") -> Publisher | None:
    if name is None:
        return None
    try:
        registered = _PUBLISHERS[name]
    except KeyError:
        known = ", ".join(sorted(_PUBLISHERS)) or "(none)"
        raise ValueError(f"unknown publisher {name!r}; known publishers: {known}") from None
    if isinstance(registered, str):
        module, _, attribute = registered.partition(":")
        registered = getattr(importlib.import_module(module), attribute)()
        _PUBLISHERS[name] = registered
    return registered

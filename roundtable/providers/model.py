"""Provider-neutral repository and change-request identity."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, order=True)
class ProviderId:
    value: str

    def __post_init__(self) -> None:
        if not self.value or self.value.strip() != self.value:
            raise ValueError("provider id must be a non-empty trimmed string")

    def __str__(self) -> str:
        return self.value


def _extensions(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class RepositoryIdentity:
    provider: ProviderId
    host: str
    locator: str
    display_name: str
    canonical_url: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.host or not self.locator or not self.display_name:
            raise ValueError("repository identity requires host, locator, and display name")
        object.__setattr__(self, "extensions", _extensions(self.extensions))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": str(self.provider),
            "host": self.host,
            "locator": self.locator,
            "displayName": self.display_name,
        }
        if self.canonical_url is not None:
            payload["canonicalUrl"] = self.canonical_url
        if self.extensions:
            payload["extensions"] = dict(self.extensions)
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> RepositoryIdentity:
        if not isinstance(payload, dict):
            raise ValueError("repository identity must be an object")
        extensions = payload.get("extensions") or {}
        if not isinstance(extensions, dict):
            raise ValueError("repository identity extensions must be an object")
        return cls(
            provider=ProviderId(_required_string(payload, "provider")),
            host=_required_string(payload, "host"),
            locator=_required_string(payload, "locator"),
            display_name=_required_string(payload, "displayName"),
            canonical_url=_optional_string(payload, "canonicalUrl"),
            extensions=extensions,
        )


@dataclass(frozen=True)
class ChangeRequestIdentity:
    repository: RepositoryIdentity
    locator: str
    display_id: str
    canonical_url: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.locator or not self.display_id:
            raise ValueError("change request identity requires locator and display id")
        object.__setattr__(self, "extensions", _extensions(self.extensions))


@dataclass(frozen=True)
class RevisionProvenance:
    source_revision: str
    base_revision: str
    source_ref: str | None = None
    base_ref: str | None = None


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"repository identity {key} must be a non-empty string")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"repository identity {key} must be a string or null")
    return value

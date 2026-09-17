"""Provider capabilities for adoption recording and collection."""

from __future__ import annotations

from typing import Protocol

from .model import AdoptionRecord


class UnsupportedAdoptionOperation(RuntimeError):
    pass


class AdoptionProvider(Protocol):
    name: str

    def record(self, session_dir: str) -> None: ...

    def collect(self, **filters: object) -> tuple[AdoptionRecord, ...]: ...

    def retry(self, session_dir: str) -> None: ...

    def resolve_link(self, record: AdoptionRecord) -> str | None: ...

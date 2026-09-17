"""Neutral adoption collection results and Azure DevOps compatibility exports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .model import AdoptionRecord


@dataclass(frozen=True)
class CollectionResult:
    records: tuple[AdoptionRecord, ...]
    diagnostics: tuple[dict[str, object], ...]

    def jsonl(self) -> str:
        return "".join(
            json.dumps(
                record.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
            for record in self.records
        )


class Collector(Protocol):
    def collect(self, **filters: object) -> CollectionResult: ...


def ado_auth_header() -> str | None:
    """Compatibility patch point; credential resolution is owned by the ADO adapter."""
    from roundtable.ado import ado_auth_header as resolve

    return resolve()


class AdoptionCollector:
    """Compatibility constructor for the Azure DevOps collector."""

    def __new__(cls, *args: Any, **kwargs: Any) -> object:
        from roundtable.ado import AzureDevOpsAdoptionCollector

        kwargs.setdefault("auth_resolver", ado_auth_header)
        return AzureDevOpsAdoptionCollector(*args, **kwargs)


def collect_to(*args: Any, **kwargs: Any) -> Any:
    from roundtable.ado import collect_adoption_to

    return collect_adoption_to(*args, **kwargs)


def write_collection(result: CollectionResult, output: str | Path | None) -> None:
    rendered = result.jsonl()
    if output is None:
        print(rendered, end="")
        return
    Path(output).write_text(rendered, encoding="utf-8")


__all__ = [
    "AdoptionCollector",
    "CollectionResult",
    "Collector",
    "ado_auth_header",
    "collect_to",
    "write_collection",
]

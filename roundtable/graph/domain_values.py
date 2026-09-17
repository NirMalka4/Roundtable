"""Typed, configuration-scoped ordered domain values."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DomainValues:
    """The effective ordered values declared by one configuration."""

    entries: tuple[tuple[str, tuple[str, ...]], ...]

    def get(self, term: str) -> tuple[str, ...] | None:
        return dict(self.entries).get(term)

    def require(self, term: str, *, feature: str) -> tuple[str, ...]:
        values = self.get(term)
        if values is None:
            raise ValueError(
                f"{feature} requires ordered domain values for {term!r}; "
                "declare `domain_values` in agent_graph.yaml"
            )
        return values

    def to_dict(self) -> dict[str, list[str]]:
        return {term: list(values) for term, values in self.entries}

    def fingerprint_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


def load_domain_values(document: dict[str, Any], root: Path) -> DomainValues | None:
    """Resolve the optional declaration into its effective typed value set."""
    declaration = document.get("domain_values")
    if declaration is None:
        return None
    assert isinstance(declaration, dict)
    if "values" in declaration:
        raw = declaration["values"]
    else:
        schema_root = (root / "schemas").resolve()
        schema_path = (schema_root / declaration["schema"]).resolve()
        if schema_root != schema_path and schema_root not in schema_path.parents:
            raise ValueError(f"domain_values.schema escapes configuration schemas: {schema_path}")
        try:
            schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as err:
            raise ValueError(f"cannot load domain values schema {schema_path}: {err}") from err
        definitions = schema.get("$defs") if isinstance(schema, dict) else None
        if not isinstance(definitions, dict):
            raise ValueError(f"domain values schema has no `$defs` mapping: {schema_path}")
        raw = {
            term: spec["enum"]
            for term, spec in definitions.items()
            if isinstance(spec, dict) and isinstance(spec.get("enum"), list)
        }
        if not raw:
            raise ValueError(f"domain values schema has no direct enum definitions: {schema_path}")
    return DomainValues(
        tuple((str(term), tuple(str(value) for value in values)) for term, values in raw.items())
    )

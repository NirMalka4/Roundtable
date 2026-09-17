"""Structural meta-validation of a config `agent_graph.yaml`.

Engine-core Layer 0: proves the raw YAML is a *well-formed config document*
(right field set, nesting and scalar types) BEFORE any semantic layer runs. It
catches typos and stray/dead keys — e.g. a field removed in code but left behind
in the bundle — that the tolerant loaders would otherwise silently drop.

The grammar lives in :data:`META_SCHEMA_PATH` (`config_meta.schema.yaml`), kept in
lockstep with :func:`agent_graph.entry_to_dict`. Errors are formatted through the
same deterministic jsonschema machinery the OVG gates use, so doctor renders them
identically to every other structural failure.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from roundtable.bundle import graph_path
from roundtable.validation import compile_validator, format_schema_errors

META_SCHEMA_PATH = Path(__file__).resolve().parent / "config_meta.schema.yaml"


def _load_meta_schema() -> dict[str, Any]:
    doc = yaml.safe_load(META_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"meta-schema root must be a mapping: {META_SCHEMA_PATH}")
    return doc


@cache
def _meta_validator():
    return compile_validator(_load_meta_schema(), schema_dir=META_SCHEMA_PATH.parent)


def _duplicate_agent_key_diagnostics(data: Any) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
        return []
    first_indexes: dict[str, int] = {}
    diagnostics: list[str] = []
    for index, agent in enumerate(data["agents"]):
        if not isinstance(agent, dict) or not isinstance(agent.get("key"), str):
            continue
        key = agent["key"]
        if key in first_indexes:
            diagnostics.append(
                f"config structure: agents[{index}].key: duplicate agent key {key!r}; "
                f"first declared at agents[{first_indexes[key]}].key"
            )
        else:
            first_indexes[key] = index
    return diagnostics


def validate_config_document(data: Any) -> list[str]:
    diagnostics = format_schema_errors(list(_meta_validator().iter_errors(data)))
    structural = [f"config structure: {d.path or '<root>'}: {d.message}" for d in diagnostics]
    return [*structural, *_duplicate_agent_key_diagnostics(data)]


def validate_config_meta(path: Path | None = None) -> list[str]:
    """Validate the raw config YAML against the engine meta-schema.

    Returns a list of ``config structure: <path>: <message>`` strings (empty ⇒
    well-formed). Reads the config from ``path`` or the resolved ``graph_path()``.
    """
    src = path or graph_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    return validate_config_document(data)

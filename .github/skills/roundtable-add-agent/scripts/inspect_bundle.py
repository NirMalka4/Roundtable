#!/usr/bin/env python3
"""Emit deterministic evidence about one explicit Roundtable configuration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from roundtable.bundle import resolve_bundle  # noqa: E402
from roundtable.capabilities import builtin_tool_names  # noqa: E402
from roundtable.engine import NODE_HANDLERS  # noqa: E402
from roundtable.graph import (  # noqa: E402
    Configuration,
    finding_producing_agent_keys,
    register_config_plugins,
)
from roundtable.mcp import (  # noqa: E402
    mcp_server_placeholders,
    mcp_server_specs,
    server_bindings,
    server_tool_names,
)
from roundtable.validation import (  # noqa: E402
    compile_validator,
    load_gate_registry,
    load_schema_document,
)


def _schema_evidence(config: Configuration, relpath: str | None) -> dict[str, Any] | None:
    if relpath is None:
        return None
    schema_root = config.root / "schemas"
    schema = load_schema_document(relpath, schema_root)
    properties = schema.get("properties")
    fields = properties if isinstance(properties, dict) else {}
    examples = schema.get("examples")
    if not isinstance(examples, list) or not examples:
        example_status = "absent"
    else:
        errors = list(compile_validator(schema, schema_root).iter_errors(examples[0]))
        example_status = "valid" if not errors else "invalid"
    return {
        "path": relpath,
        "topLevelFields": list(fields),
        "findingArrays": [
            name
            for name, definition in fields.items()
            if isinstance(definition, dict) and definition.get("x-finding-array") is True
        ],
        "exampleStatus": example_status,
    }


def inspect(config_arg: str, agent_key: str | None = None) -> dict[str, Any]:
    root = resolve_bundle(config_arg)
    config = Configuration.from_file(root)
    register_config_plugins(config)
    if agent_key is not None and agent_key not in config.by_key:
        raise ValueError(f"unknown agent {agent_key!r} (known: {sorted(config.by_key)})")

    entries = [entry for entry in config.entries if agent_key is None or entry.key == agent_key]
    depended_on = {edge.source for entry in config.entries for edge in entry.forward_edges}
    finding_nodes = finding_producing_agent_keys(config=config)
    gates = load_gate_registry(config.root / "gates.yaml")
    specs = mcp_server_specs()
    return {
        "configuration": {
            "name": config.name,
            "identity": config.identity,
            "root": str(config.root),
            "fingerprint": config.fingerprint,
            "domainValues": (
                config.domain_values.to_dict() if config.domain_values is not None else None
            ),
        },
        "registeredNodeKinds": sorted(NODE_HANDLERS),
        "declaredModels": sorted(
            {entry.model for entry in config.entries if entry.is_llm and entry.model}
        ),
        "builtInTools": sorted(builtin_tool_names()),
        "gates": {
            name: {
                "level": gate.default_level,
                "hint": gate.default_hint,
                "requires": list(gate.requires),
            }
            for name, gate in sorted(gates.items())
        },
        "nodes": [
            {
                "key": entry.key,
                "kind": entry.kind,
                "edges": [
                    {"from": edge.source, "required": edge.required} for edge in entry.forward_edges
                ],
                "sink": entry.key not in depended_on,
                "findingProducer": entry.key in finding_nodes,
                "schema": _schema_evidence(config, entry.output_schema),
                "ovgGates": [gate["gate"] for gate in (entry.ovg_gates or ())],
                "mcp": [binding.server for binding in entry.mcp],
                "tools": list(entry.tools) if entry.tools is not None else None,
            }
            for entry in entries
        ],
        "mcpServers": {
            name: {
                "requirements": list(spec.get("requires") or []),
                "placeholders": sorted(mcp_server_placeholders(name)),
                "bindings": [list(binding) for binding in server_bindings(name)],
                "tools": sorted(server_tool_names(name)),
            }
            for name, spec in sorted(specs.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="shipped bundle name or bundle path")
    parser.add_argument("--agent", help="limit node evidence to one graph key")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        payload = inspect(args.config, args.agent)
    except Exception as exc:
        print(f"inspect_bundle: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        config = payload["configuration"]
        print(f"{config['identity']['name']}  {config['root']}  {config['fingerprint']}")
        for node in payload["nodes"]:
            print(f"{node['key']}: kind={node['kind']} sink={node['sink']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

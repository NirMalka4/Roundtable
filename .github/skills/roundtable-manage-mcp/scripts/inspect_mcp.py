#!/usr/bin/env python3
"""Inspect Roundtable MCP registry data and one configuration's bindings."""

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
from roundtable.graph import Configuration  # noqa: E402
from roundtable.mcp import (  # noqa: E402
    mcp_server_placeholders,
    mcp_server_specs,
    server_bindings,
    server_tool_names,
)


def inspect(config_arg: str) -> dict[str, Any]:
    config = Configuration.from_file(resolve_bundle(config_arg))
    consumers: dict[str, list[dict[str, Any]]] = {}
    for entry in config.entries:
        for binding in entry.mcp:
            consumers.setdefault(binding.server, []).append(
                {
                    "agent": entry.key,
                    "usage": binding.usage,
                    "grantedTools": [
                        tool
                        for tool in (entry.tools or ())
                        if tool.startswith(f"{binding.server}/")
                    ],
                    "unrestrictedTools": entry.tools is None,
                }
            )
    return {
        "configuration": {
            "name": config.name,
            "root": str(config.root),
            "fingerprint": config.fingerprint,
        },
        "servers": {
            name: {
                "spec": spec,
                "placeholders": sorted(mcp_server_placeholders(name)),
                "requirements": list(spec.get("requires") or []),
                "advertisedBindings": [list(row) for row in server_bindings(name)],
                "toolInventory": sorted(server_tool_names(name)),
                "consumers": consumers.get(name, []),
            }
            for name, spec in sorted(mcp_server_specs().items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="shipped bundle name or bundle path")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        payload = inspect(args.config)
    except Exception as exc:
        print(f"inspect_mcp: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{payload['configuration']['name']}: {len(payload['servers'])} registered servers")
        for name, server in payload["servers"].items():
            print(f"{name}: {len(server['consumers'])} consumer(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

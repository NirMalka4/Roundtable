#!/usr/bin/env python3
"""Validate Roundtable MCP registry invariants and graph permission agreement."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from roundtable.bundle import resolve_bundle  # noqa: E402
from roundtable.graph import Configuration  # noqa: E402
from roundtable.mcp import validate_mcp_registry  # noqa: E402


def _binding_errors(config: Configuration) -> list[str]:
    errors: list[str] = []
    for entry in config.entries:
        if entry.tools is None:
            continue
        for binding in entry.mcp:
            if not any(tool.startswith(f"{binding.server}/") for tool in entry.tools):
                errors.append(
                    f"{entry.key}: provisions MCP server {binding.server!r} but grants none of "
                    f"its tools in the explicit `tools` allowlist"
                )
    return errors


def validate(config_arg: str) -> dict[str, object]:
    root = resolve_bundle(config_arg)
    config = Configuration.from_file(root)
    errors = validate_mcp_registry()
    errors.extend(_binding_errors(config))
    doctor = subprocess.run(
        [
            sys.executable,
            "-m",
            "roundtable.cli",
            "doctor",
            "--static",
            "--config",
            str(root),
        ],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    doctor_output = (doctor.stdout + doctor.stderr).strip()
    if doctor.returncode:
        errors.append(f"doctor failed ({doctor.returncode}): {doctor_output}")
    return {
        "ok": not errors,
        "configuration": config.name,
        "root": str(config.root),
        "errors": errors,
        "warnings": [],
        "doctor": doctor_output,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="shipped bundle name or bundle path")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        payload = validate(args.config)
    except Exception as exc:
        print(f"validate_mcp: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        for error in payload["errors"]:
            print(f"ERROR: {error}")
        for warning in payload["warnings"]:
            print(f"WARN: {warning}")
        print("MCP validation: PASS" if payload["ok"] else "MCP validation: FAIL")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Preview or atomically scaffold one explicit Roundtable configuration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from roundtable.engine import NODE_HANDLERS  # noqa: E402
from roundtable.graph import Configuration  # noqa: E402

DoctorRunner = Callable[[Path], tuple[int, str]]


def _required_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _required_string(mapping: dict[str, Any], key: str, owner: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{owner}.{key} must be a non-empty string")
    return value


def _build(spec: object) -> tuple[Path, dict[str, Any], dict[Path, str]]:
    top = _required_mapping(spec, "spec")
    root = Path(_required_string(top, "root", "spec")).expanduser().resolve()
    source = _required_mapping(top.get("source"), "spec.source")
    node = _required_mapping(top.get("node"), "spec.node")
    source_key = _required_string(source, "key", "spec.source")
    node_key = _required_string(node, "key", "spec.node")
    kind = _required_string(node, "kind", "spec.node")
    if kind not in NODE_HANDLERS:
        raise ValueError(
            f"spec.node.kind {kind!r} is unsupported (registered: {sorted(NODE_HANDLERS)})"
        )
    executor = _required_string(top, "executor", "spec")
    cardinality = _required_string(top, "sink_cardinality", "spec")
    if "sink" not in top:
        raise ValueError("spec.sink must be supplied explicitly (use null for no publishing)")

    source_entry = {
        "key": source_key,
        "kind": "source",
        "emoji": _required_string(source, "emoji", "spec.source"),
    }
    node_entry: dict[str, Any] = {
        "key": node_key,
        "kind": kind,
        "emoji": _required_string(node, "emoji", "spec.node"),
        "agent_id": _required_string(node, "agent_id", "spec.node"),
        "display_name": _required_string(node, "display_name", "spec.node"),
        "edges": [{"from": source_key, "required": True}],
    }
    files: dict[Path, str] = {}
    if kind == "llm":
        description = _required_string(node, "description", "spec.node")
        body = _required_string(node, "prompt_body", "spec.node")
        model = _required_string(node, "model", "spec.node")
        schema = _required_mapping(node.get("output_schema"), "spec.node.output_schema")
        gates = node.get("ovg_gates")
        if not isinstance(gates, list) or not gates:
            raise ValueError("spec.node.ovg_gates must be a non-empty list")
        prompt_rel = Path("prompts") / "Reviewer" / "Agents" / f"{node_key}.agent.md"
        schema_rel = Path("schemas") / f"{node_key}.schema.yaml"
        node_entry.update(
            {
                "system_prompt": {"instructions": f"Agents/{node_key}.agent.md"},
                "model": model,
                "output_schema": f"{node_key}.schema.yaml",
                "ovg_gates": gates,
            }
        )
        for optional in ("tools", "mcp", "timeout_seconds"):
            if optional in node:
                node_entry[optional] = node[optional]
        files[root / prompt_rel] = (
            "---\n"
            + yaml.safe_dump(
                {"description": description},
                sort_keys=False,
                allow_unicode=True,
                width=100,
            )
            + "---\n\n"
            + body.rstrip()
            + "\n"
        )
        files[root / schema_rel] = yaml.safe_dump(
            schema,
            sort_keys=False,
            allow_unicode=True,
            width=100,
        )
    elif kind in {"code", "reducer"}:
        node_entry["code_fn"] = _required_string(node, "code_fn", "spec.node")
    else:
        raise ValueError(
            f"scaffolding kind {kind!r} requires fields not represented by this spec; "
            "provide an llm, code, or reducer initial node"
        )

    document: dict[str, Any] = {
        "name": _required_string(top, "name", "spec"),
        "executor": executor,
        "sink": top.get("sink"),
        "sink_cardinality": cardinality,
        "agents": [source_entry, node_entry],
    }
    for optional in (
        "projector",
        "report",
        "plugins",
        "branding",
        "max_steps",
        "domain_values",
        "publishing",
    ):
        if optional in top:
            document[optional] = top[optional]
    Configuration.from_document(document, root=root)

    files[root / "agent_graph.yaml"] = yaml.safe_dump(
        document,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    files[root / "gates.yaml"] = "gates: {}\n"
    for target in files:
        if target != root and root not in target.resolve().parents:
            raise ValueError(f"generated path escapes configuration root: {target}")
        if target.exists():
            raise FileExistsError(f"refusing to overwrite existing target: {target}")
    for module in document.get("plugins") or ():
        rel = Path(*str(module).split("."))
        candidates = (root / f"{rel}.py", root / rel / "__init__.py")
        if not any(candidate.is_file() for candidate in candidates):
            raise ValueError(f"referenced plugin module has no existing file under root: {module}")
    return root, document, files


def _doctor(root: Path) -> tuple[int, str]:
    result = subprocess.run(
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
    return result.returncode, (result.stdout + result.stderr).strip()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def scaffold(
    spec: object,
    *,
    write: bool,
    doctor_runner: DoctorRunner = _doctor,
) -> dict[str, Any]:
    root, document, files = _build(spec)
    payload: dict[str, Any] = {
        "status": "preview",
        "root": str(root),
        "configuration": document,
        "files": [str(path) for path in sorted(files)],
    }
    if not write:
        return payload

    created: list[Path] = []
    try:
        for path, content in files.items():
            _atomic_write(path, content)
            created.append(path)
        code, output = doctor_runner(root)
        if code:
            raise RuntimeError(f"doctor failed ({code}): {output}")
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    payload.update(status="written", doctor=output)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="JSON spec file")
    parser.add_argument("--write", action="store_true", help="write after preview validation")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        payload = scaffold(spec, write=args.write)
    except Exception as exc:
        print(f"scaffold_config: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

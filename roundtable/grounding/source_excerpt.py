"""Derive a finding anchor's ``excerpt`` from the review checkout.

A reviewer that quotes source lines can paraphrase, elide (``...``), or newline-join
what it read — the excerpt is a **model self-report** nothing verifies. This module
makes the excerpt **faithful by construction**: for any anchor that cites a file and a
line span, the engine reads those exact bytes from the checkout and overwrites the
model's excerpt. No gate, no retry — paraphrase simply becomes impossible.

**Opt-in, generic, no hardcoded field names.** Grounding activates only for a schema
whose finding array declares an ``excerpt`` sub-key under its
``x-finding-adapter.locations`` mapping (the same mapping the extractor uses to locate
``filePath``/``startLine``/``endLine``). The raw model output at this stage still spells
its arrays with the schema's native keys (the ``anchors → locations`` adapter runs at
extraction, *after* this), so those adapter keys address the raw JSON directly.

**Non-file anchors are left untouched.** An anchor with a null/absent ``filePath``
(a PR/work-item/dependency-graph locus) has no lines to read, so the model's excerpt
stands (lower assurance; nothing can verify it).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from roundtable.bundle import schema_dir
from roundtable.utils import extract_json


@dataclass(frozen=True)
class _ExcerptSpec:
    """How one finding array spells the fields excerpt grounding needs."""

    finding_array: str
    anchor_array: str
    file_key: str
    start_key: str
    end_key: str
    excerpt_key: str


def _specs_from_schema(schema: Mapping[str, Any]) -> tuple[_ExcerptSpec, ...]:
    """The excerpt-grounding specs declared by ``schema`` (empty ⇒ grounding off)."""
    props = schema.get("properties")
    if not isinstance(props, Mapping):
        return ()
    specs: list[_ExcerptSpec] = []
    for name, spec in props.items():
        if not isinstance(spec, Mapping) or not spec.get("x-finding-array"):
            continue
        adapter = spec.get("x-finding-adapter")
        loc = adapter.get("locations") if isinstance(adapter, Mapping) else None
        if not isinstance(loc, Mapping):
            continue
        excerpt_key = loc.get("excerpt")
        if not isinstance(excerpt_key, str) or not excerpt_key:
            continue
        specs.append(
            _ExcerptSpec(
                finding_array=name,
                anchor_array=loc.get("from", "locations"),
                file_key=loc.get("filePath", "filePath"),
                start_key=loc.get("startLine", "startLine"),
                end_key=loc.get("endLine", "endLine"),
                excerpt_key=excerpt_key,
            )
        )
    return tuple(specs)


def _specs_for_schema_name(
    schema_name: str,
    schema_root: Path | None = None,
) -> tuple[_ExcerptSpec, ...]:
    """Load ``schema_name`` under the active config's schema dir and read its specs.

    Not cached: the active schema dir switches with ``ROUNDTABLE_CONFIG_ROOT`` (a
    same-named schema differs across config bundles), and the read is trivial next to
    an agent subprocess.
    """
    try:
        raw = ((schema_root or schema_dir()) / schema_name).read_text(encoding="utf-8")
        schema = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError):
        return ()
    return _specs_from_schema(schema) if isinstance(schema, Mapping) else ()


def _read_span(workspace_root: Path, rel_path: str, start: Any, end: Any) -> str | None:
    """The exact bytes of ``rel_path`` lines ``start..end`` (1-indexed, inclusive).

    Returns ``None`` — leaving the model's excerpt in place — when the path escapes the
    workspace, the file is missing/undecodable, or the span is out of range. Path
    *existence* is a separate concern (the ``grounded_locations`` gate), so failing open
    here leaves no hole.
    """
    if not rel_path or not isinstance(start, int) or not isinstance(end, int):
        return None
    if isinstance(start, bool) or isinstance(end, bool) or start < 1 or end < start:
        return None
    try:
        root = workspace_root.resolve(strict=False)
        target = (root / rel_path).resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return None
    if target != root and root not in target.parents:
        return None  # absolute path, ``..`` traversal, or symlink escape
    try:
        if not target.is_file():
            return None
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    if end > len(lines):
        return None
    return "\n".join(lines[start - 1 : end])


def _ground_findings(data: Mapping[str, Any], spec: _ExcerptSpec, root: Path) -> bool:
    findings = data.get(spec.finding_array)
    if not isinstance(findings, list):
        return False
    changed = False
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        anchors = finding.get(spec.anchor_array)
        if not isinstance(anchors, list):
            continue
        for anchor in anchors:
            if not isinstance(anchor, dict):
                continue
            file_path = anchor.get(spec.file_key)
            if not isinstance(file_path, str) or not file_path:
                continue  # non-file anchor: keep the model's excerpt
            derived = _read_span(
                root, file_path, anchor.get(spec.start_key), anchor.get(spec.end_key)
            )
            if derived is None:
                continue  # fail open (grounded_locations owns existence)
            if anchor.get(spec.excerpt_key) != derived:
                anchor[spec.excerpt_key] = derived
                changed = True
    return changed


def ground_source_excerpts(
    response: str,
    schema_name: str | None,
    workspace_root: str | Path | None,
    schema_root: Path | None = None,
) -> str:
    """Return ``response`` with every file-anchor excerpt replaced by checkout bytes.

    A no-op (returns ``response`` unchanged, byte-for-byte) when: no schema is given,
    no workspace is given, the schema declares no excerpt grounding, the response is not
    parseable JSON, or nothing needed rewriting. Only when at least one excerpt actually
    changes is the response re-serialized as canonical JSON (the agent's output contract
    is strict JSON, so downstream parsing is unaffected).
    """
    if not schema_name or not workspace_root:
        return response
    return ground_response_excerpts(
        response,
        _specs_for_schema_name(schema_name, schema_root),
        workspace_root,
    )


def ground_response_excerpts(
    response: str,
    specs: tuple[_ExcerptSpec, ...],
    workspace_root: str | Path | None,
) -> str:
    """Core rewrite: apply already-resolved ``specs`` to ``response`` (schema-free seam)."""
    if not specs or not workspace_root:
        return response
    try:
        data = json.loads(extract_json(response))
    except (json.JSONDecodeError, TypeError, ValueError):
        return response
    if not isinstance(data, dict):
        return response
    root = Path(workspace_root)
    changed = False
    for spec in specs:
        if _ground_findings(data, spec, root):
            changed = True
    if not changed:
        return response
    return json.dumps(data, ensure_ascii=False, indent=2)

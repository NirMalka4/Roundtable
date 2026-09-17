"""Output-shape hint construction — the agent's schema turned into a prompt anchor.

``build_output_contract`` is the public entry point for the SYSTEM prompt: it wraps the
schema-derived anchor under an ``## Output contract`` heading (+ the schema's optional
``preface``) so the stable contract lives in the system prompt, not the per-attempt
context. ``build_schema_hint`` returns the same schema-derived anchor body (no heading)
and is kept for tests. Both resolve the agent's declarative ``output_schema`` (via the
graph) and render from three schema-derived parts:

  1. the top-level ``required`` keys (the "must contain" line),
  2. the canonical ``$def`` descriptions of the vocabulary terms the schema
     references (the field-meanings block, expanded transitively), and
  3. the canonical ``examples[0]`` / declared ``output_example`` (the shape to imitate).

Everything here is pure and derived from config — there is no per-prompt copy and no
Python constant table. The schema is the single source of truth.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roundtable.bundle import schema_dir
from roundtable.validation import SchemaLoadError, load_schema_document

if TYPE_CHECKING:
    from roundtable.graph import Configuration, GraphEntry

_VOCABULARY_REF = "_shared/vocabulary.schema.yaml"


def _schema_root(root: Path | None) -> Path:
    return root or schema_dir()


def _resolve_output_example(
    entry: GraphEntry,
    schema: dict,
    schema_root: Path | None = None,
) -> Any:
    """The canonical shape the output hint imitates.

    Prefers the agent's declarative ``output_example`` document (used by agents
    sharing a minimal schema to advertise their own richer shape); falls back to
    the schema's own ``examples[0]``. A missing/unloadable example file is never
    fatal — the hint just falls back to the schema example (or the required line)."""
    if entry.output_example:
        try:
            return load_schema_document(entry.output_example, _schema_root(schema_root))
        except SchemaLoadError:
            pass
    examples = schema.get("examples") or []
    return examples[0] if examples else None


@cache
def _vocabulary_defs(schema_root: Path) -> dict[str, Any]:
    """The canonical vocabulary ``$defs`` (name → definition), in declaration order."""
    try:
        vocab = load_schema_document(f"{_VOCABULARY_REF}", schema_root)
    except SchemaLoadError:
        return {}
    defs = vocab.get("$defs")
    return defs if isinstance(defs, dict) else {}


def _collect_vocab_refs(node: Any, out: set[str]) -> None:
    """Collect every vocabulary ``$def`` name referenced by ``$ref`` anywhere in ``node``.

    Matches both an agent schema's fully-qualified ref
    (``_shared/vocabulary.schema.yaml#/$defs/<term>``) and a within-vocabulary
    fragment ref (``#/$defs/<term>``) — both contain ``#/$defs/``.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and "#/$defs/" in value:
                out.add(value.split("#/$defs/")[-1])
            else:
                _collect_vocab_refs(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_vocab_refs(item, out)


def _referenced_vocab_terms(schema: dict, schema_root: Path | None = None) -> set[str]:
    """The vocabulary terms an agent schema uses, expanded transitively.

    A term whose own definition refs another term (e.g. ``exploitability`` →
    ``exploitability_rating``, ``original_severity`` → ``severity``) pulls that term
    in too, so the agent sees the meaning of every canonical term in play.
    """
    root = _schema_root(schema_root)
    defs = _vocabulary_defs(root)
    used: set[str] = set()
    _collect_vocab_refs(schema, used)
    frontier = list(used)
    while frontier:
        term = frontier.pop()
        nested: set[str] = set()
        _collect_vocab_refs(defs.get(term, {}), nested)
        for dep in nested - used:
            used.add(dep)
            frontier.append(dep)
    return {term for term in used if term in defs}


def _field_meanings_block(schema: dict, schema_root: Path | None = None) -> str:
    """A block defining each canonical term the agent uses, in vocabulary order.

    Surfaces each referenced ``$def``'s one-line ``description`` so the canonical
    meaning (including enum semantics) reaches the agent without any per-prompt copy.
    Returns ``''`` when the schema references no described vocabulary term.
    """
    root = _schema_root(schema_root)
    defs = _vocabulary_defs(root)
    used = _referenced_vocab_terms(schema, root)
    lines: list[str] = []
    for term, definition in defs.items():
        if term not in used:
            continue
        description = definition.get("description")
        if isinstance(description, str) and description.strip():
            lines.append(f"- {term}: {' '.join(description.split())}")
    if not lines:
        return ""
    return "\nField meanings (canonical vocabulary — use these terms exactly):\n" + "\n".join(lines)


def _iter_field_descriptions(node: Any, prefix: str = ""):
    """Yield ``(path, description)`` for every property carrying an inline ``description``.

    Walks ``properties`` and array ``items`` to any depth (``findings[].locations[].role``),
    stopping at a ``$ref`` (its target is documented where it is defined — the vocabulary
    ``$def`` block surfaces those). This is what makes a SELF-CONTAINED schema's per-field
    prose (the contract extracted out of the agent body) reach the agent."""
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            if not isinstance(sub, dict):
                continue
            path = f"{prefix}{name}"
            desc = sub.get("description")
            if isinstance(desc, str) and desc.strip():
                yield path, " ".join(desc.split())
            if "$ref" not in sub:
                yield from _iter_field_descriptions(sub, f"{path}.")
    items = node.get("items")
    if isinstance(items, dict) and "$ref" not in items:
        arr_prefix = f"{prefix[:-1]}[]." if prefix.endswith(".") else f"{prefix}[]."
        yield from _iter_field_descriptions(items, arr_prefix)


def _inline_field_meanings_block(schema: dict) -> str:
    """Surface each field's own inline ``description`` (self-contained contracts).

    Complements :func:`_field_meanings_block` (vocabulary ``$def`` descriptions): a
    schema that inlines its field definitions carries the contract prose on the fields
    themselves, and this block delivers it to the agent. Returns ``''`` when no field
    declares an inline description.

    The heading carries no parenthetical: this block is already rendered under the
    agent's ``## Output contract`` heading, so naming it again is meta-text the agent
    must read past. Its vocabulary sibling keeps one only because that parenthetical
    is an instruction ("use these terms exactly"), not a label."""
    lines = [f"- {path}: {desc}" for path, desc in _iter_field_descriptions(schema)]
    if not lines:
        return ""
    return "\nField meanings:\n" + "\n".join(lines)


def _render_schema_hint(entry: GraphEntry, schema: dict, schema_root: Path) -> str:
    """Render the schema-derived output anchor body (no heading, no preface).

    A generic output-format directive, then — from the agent's ``output_schema`` —
    its top-level ``required`` keys ("must contain" line), the vocabulary ``$def``
    descriptions of the terms it references (canonical field meanings), and its
    ``examples[0]`` / declared ``output_example`` (the shape to imitate). Returns
    ``''`` when the schema declares neither ``required`` nor an example."""
    required = list(schema.get("required", []))
    example = _resolve_output_example(entry, schema, schema_root)
    if example is None and not required:
        return ""

    parts: list[str] = [
        "\nCall `roundtable_submit_output` with your complete result under its `output` "
        "argument. If validation rejects the call, apply the exact correction and call "
        "the tool again in the same turn. A successful call ends the turn. Do not print, "
        "repeat, or wrap the submitted JSON in assistant text."
    ]
    if required:
        strict_root = schema.get("additionalProperties") is False
        key_clause = (
            "MUST contain these keys — and may otherwise use ONLY keys declared in the "
            "schema below (no additional or invented keys)"
            if strict_root
            else "MUST contain at least these keys (additional keys are allowed)"
        )
        parts.append(
            "\nYour JSON root object "
            + key_clause
            + ": "
            + json.dumps(required, separators=(",", ":"), ensure_ascii=False)
            + "."
        )
    field_meanings = _field_meanings_block(schema, schema_root)
    if field_meanings:
        parts.append(field_meanings)
    inline_meanings = _inline_field_meanings_block(schema)
    if inline_meanings:
        parts.append(inline_meanings)
    if example is not None:
        parts.append(
            "\nCanonical example (match this SHAPE; replace the values with your own analysis):\n"
            + json.dumps(example, indent=2, ensure_ascii=False)
        )
    return "\n".join(parts)


def build_schema_hint(agent_name: str, configuration: Configuration) -> str:
    """The schema-derived output anchor for ``agent_name`` (no heading/preface).

    Resolves the agent's declarative ``output_schema`` via the graph and delegates to
    :func:`_render_schema_hint`. Returns ``''`` when the agent has no schema."""
    entry = configuration.by_key.get(agent_name)
    if entry is None or entry.output_schema is None:
        return ""
    schema_root = configuration.root / "schemas"
    return _render_schema_hint(
        entry,
        load_schema_document(entry.output_schema, schema_root),
        schema_root,
    )


def build_output_contract(entry: GraphEntry, schema_root: Path | None = None) -> str:
    """The agent's STABLE output contract, for the SYSTEM prompt.

    Wraps the schema-derived hint under an ``## Output contract`` heading, preceded by
    the schema's optional ``preface`` (the WHY of the shape). Returns ``''`` when the
    agent declares no schema or the schema yields no required/example anchor — the
    heading is then suppressed. This is the stable contract that belongs in the system
    prompt (it never varies per attempt); attempt-specific gate-error feedback stays in
    the context payload."""
    if entry.output_schema is None:
        return ""
    root = _schema_root(schema_root)
    schema = load_schema_document(entry.output_schema, root)
    body = _render_schema_hint(entry, schema, root)
    if not body:
        return ""
    parts = ["## Output contract"]
    preface = schema.get("preface")
    if isinstance(preface, str) and preface.strip():
        parts.append(preface.strip())
    parts.append(body.lstrip("\n"))
    return "\n\n".join(parts)

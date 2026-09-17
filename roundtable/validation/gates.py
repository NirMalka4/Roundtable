"""OVG gate registry — the config-agnostic mechanism, not the gates themselves.

This module owns the *machinery*: the schema loader, the deterministic error
formatter, the ``fn``-name→callable registry, and the two task-agnostic gate
primitives. Each agent's output contract is expressed as:

  * one **JSON Schema (Draft 2020-12)** per agent (``configs/<bundle>/schemas/<agent>.yaml``),
    the single source of truth for the agent's output shape — it drives BOTH the
    structural ``json_schema`` gate AND the LLM output-format hint; and
  * a **named gate registry** (``configs/<bundle>/gates.yaml``) mapping each gate to a
    reusable callable plus its declarative defaults (``default_level``,
    ``default_hint``) and the field-paths it reads (``requires``, which the doctor
    uses for schema<->gate coherence).

Design invariants (see ``AGENTS.md`` at the repo root for the full contract):

  * Gate functions return **NEUTRAL** diagnostics — a ``Diagnostic`` carries only a
    location + message, never a severity. The *runner* stamps the configured level
    (registry default, per-agent override). Single owner of level.
  * Contract data (levels, hints, requires, schemas) lives in declarative config,
    never in Python constant tables. Only the ``fn`` name→callable dispatch and
    pure message-formatting logic live here as code.
  * **A gate that encodes a review contract belongs to the bundle that declares it**,
    not here. ``_GATE_FUNCTIONS`` is a floor of two primitives; a bundle adds its own
    via :func:`register_gate_function` from its ``plugins:`` module. The reverse
    import is forbidden and ``roundtable doctor`` proves it
    (``roundtable/validation_boundary.py``). What a gate may depend on is
    :mod:`.gate_kit`, the stable public surface for gate authors.
  * Structural validation is itself a first-class gate (``json_schema``) in the same
    ``{level, hint}`` model, so a structural failure has a coherent hint source.
  * ``jsonschema`` errors are formatted into project-owned, deterministic messages —
    never fed raw to the model. Remote ``$ref`` is disabled; only local
    ``schemas/_shared/*`` fragments resolve.

The registry is consumed by the runtime OVG engine ``validation/pipeline.run_pipeline``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from roundtable.bundle import config_root, gates_manifest, hint_dir, schema_dir

from .gate_kit import (
    Diagnostic,
    GateFn,
    GateRequest,
)
from .gate_kit import (
    path_in_changed_files as _path_in_changed_files,
)


# ─── Bundle locations (resolved through the config-root seam; cwd-independent) ──
# Call-time so an override / ROUNDTABLE_CONFIG_ROOT is honored. The legacy
# module-constant names (_SCHEMA_DIR, _HINT_DIR, _GATES_MANIFEST, _CONFIG_DIR) remain
# available as call-time attributes via __getattr__ below for external importers.
def _schema_dir() -> Path:
    return schema_dir()


def _hint_dir() -> Path:
    return hint_dir()


def _gates_manifest() -> Path:
    return gates_manifest()


# ─── Engine-core universal gate manifest + hints (NOT per-config) ──────────────
# The task-agnostic primitives (json_schema, …) live beside this module, so they
# apply to EVERY bundle regardless of config_root. Domain gates/hints resolve
# through the config-root seam above; universal ones resolve here.
_CORE_GATES_MANIFEST = Path(__file__).resolve().parent / "gates_core.yaml"
_CORE_HINT_DIR = Path(__file__).resolve().parent / "hints_core"


def __getattr__(name: str) -> Path:  # PEP 562: keep legacy constant names working
    _legacy = {
        "_CONFIG_DIR": config_root,
        "_SCHEMA_DIR": schema_dir,
        "_HINT_DIR": hint_dir,
        "_GATES_MANIFEST": gates_manifest,
    }
    resolver = _legacy.get(name)
    if resolver is not None:
        return resolver()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class SchemaLoadError(Exception):
    """Raised when a schema document or a ``$ref`` cannot be resolved locally."""


# ─────────────────────────────────────────────────────────────────────────────
# Gate specs (neutral diagnostics + the GateFn contract live in gate_kit)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateSpec:
    """A registered gate: its callable + declarative defaults from ``gates.yaml``."""

    name: str
    fn: GateFn
    default_level: str  # 'error' | 'warn'
    default_hint: str | None  # hint name (resolvable via load_hint), or None
    requires: tuple[str, ...]  # field-paths the gate reads (drives doctor coherence)
    accepts_arbitrary_json_root: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Schema loading — local-only $ref, remote disabled
# ─────────────────────────────────────────────────────────────────────────────


def load_schema_document(relpath: str, schema_dir: Path | None = None) -> dict[str, Any]:
    """Load a single JSON-Schema YAML document by path relative to ``schema_dir``."""
    base = (schema_dir or _schema_dir()).resolve()
    target = (base / relpath).resolve()
    if base != target and base not in target.parents:
        raise SchemaLoadError(f"schema path escapes schema dir: {relpath!r}")
    if not target.is_file():
        raise SchemaLoadError(f"schema not found: {target}")
    stat = target.stat()
    doc = _load_schema_file(target, stat.st_mtime_ns, stat.st_size)
    if not isinstance(doc, dict):
        raise SchemaLoadError(f"schema root must be a mapping: {target}")
    return doc


@cache
def _load_schema_file(target: Path, _mtime_ns: int, _size: int) -> object:
    return yaml.safe_load(target.read_text(encoding="utf-8"))


def _make_registry(schema_dir: Path) -> Registry:
    """Build a ``referencing`` registry that resolves ``$ref`` from local files only.

    Any URI with a scheme (``http://`` …) is rejected — no network, no surprises.
    Relative refs resolve to sibling files under ``schema_dir`` (segment-confined).
    """
    base = schema_dir.resolve()

    def retrieve(uri: str) -> Resource[Any]:
        if "://" in uri:
            raise SchemaLoadError(f"remote $ref is disabled: {uri!r}")
        relpath = uri.split("#", 1)[0]
        doc = load_schema_document(relpath, schema_dir=base)
        return Resource.from_contents(doc, default_specification=DRAFT202012)

    return Registry(retrieve=retrieve)


def compile_validator(
    schema: dict[str, Any], schema_dir: Path | None = None
) -> Draft202012Validator:
    """Compile a Draft 2020-12 validator with local-only ``$ref`` resolution."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return _compile_validator(canonical, (schema_dir or _schema_dir()).resolve())


@cache
def _compile_validator(canonical: str, schema_dir: Path) -> Draft202012Validator:
    schema = json.loads(canonical)
    _assert_no_remote_refs(schema)
    registry = _make_registry(schema_dir)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _assert_no_remote_refs(node: Any) -> None:
    """Reject any ``$ref`` with a URI scheme (``http(s)://`` …) — network is disabled.

    Eager top-level scan so the failure is a clear project-owned ``SchemaLoadError``
    rather than a ``jsonschema``-wrapped ``Unresolvable`` surfaced lazily at validate
    time. Local ``$ref`` fragments are additionally guarded in the registry retrieve.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and "://" in ref:
            raise SchemaLoadError(f"remote $ref is disabled: {ref!r}")
        for value in node.values():
            _assert_no_remote_refs(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_remote_refs(item)


# ─────────────────────────────────────────────────────────────────────────────
# Project-owned, deterministic jsonschema error formatting
# ─────────────────────────────────────────────────────────────────────────────


def _json_path(error: Any) -> str:
    """Render a jsonschema error location as ``a.b[0].c`` (root = "")."""
    parts: list[str] = []
    for token in error.absolute_path:
        if isinstance(token, int):
            parts.append(f"[{token}]")
        else:
            parts.append(f".{token}" if parts else str(token))
    return "".join(parts)


def _shape_hint(subschema: Any) -> str:
    """Compact description of a ``contains`` subschema, for a diagnostic message.

    Summarises the properties the subschema pins to a ``const`` — the discriminator a
    schema uses to require a tagged member (e.g. an anchor whose ``role`` is ``seed``).
    Without that, jsonschema's own message echoes the whole failing array, which is
    unreadable as retry feedback. Falls back when nothing is pinned.
    """
    props = subschema.get("properties") if isinstance(subschema, dict) else None
    pinned = {
        name: spec["const"]
        for name, spec in (props or {}).items()
        if isinstance(spec, dict) and "const" in spec
    }
    if pinned:
        return ", ".join(f"{name}={value!r}" for name, value in pinned.items())
    return "the required shape"


def _describe(error: Any) -> str:
    """Deterministic, project-owned message for a jsonschema error.

    Keyword→phrasing is formatting logic (code), not contract data. Falls back to a
    trimmed jsonschema message for keywords without a bespoke phrasing so nothing is
    silently dropped — but the common cases read cleanly and stably.

    Composite keywords (``anyOf``/``oneOf``) are resolved to their most relevant
    branch error by :func:`_resolve_effective` *before* this runs, so a composite
    failure surfaces the concrete branch problem (e.g. a missing required field)
    instead of the opaque "not valid under any of the given schemas".
    """
    kw = error.validator
    if kw == "required":
        missing = _missing_required(error)
        return f"missing required field {missing!r}" if missing else str(error.message)
    if kw == "type":
        expected = error.validator_value
        expected_s = " or ".join(expected) if isinstance(expected, list) else str(expected)
        return f"expected type {expected_s}, got {_json_type(error.instance)}"
    if kw == "enum":
        allowed = ", ".join(repr(v) for v in error.validator_value)
        return f"value {error.instance!r} not in allowed set {{{allowed}}}"
    if kw == "const":
        return f"value {error.instance!r} must equal {error.validator_value!r}"
    if kw == "minItems":
        return f"array must have at least {error.validator_value} item(s)"
    if kw == "contains":
        return f"array must contain at least one item matching {_shape_hint(error.validator_value)}"
    if kw == "maxItems":
        return f"array must have at most {error.validator_value} item(s)"
    if kw == "additionalProperties":
        return str(error.message)
    if kw == "propertyNames":
        return str(error.message)
    if kw == "not":
        return f"value {error.instance!r} is not allowed here"
    if kw in ("anyOf", "oneOf"):
        # Reached only when no branch could be selected (e.g. empty context) — keep
        # the raw message rather than dropping the failure.
        return str(error.message)
    return str(error.message)


def _branch_index(error: Any) -> int | None:
    """Index into the ``anyOf``/``oneOf`` array a context sub-error came from."""
    path = list(error.schema_path)
    return path[0] if path and isinstance(path[0], int) else None


def _type_matches(inst_type: str, declared: Any) -> bool:
    decls = declared if isinstance(declared, list) else [declared]
    for d in decls:
        if d == inst_type:
            return True
        if d == "number" and inst_type == "integer":
            return True
    return False


def _select_composite_error(error: Any) -> Any | None:
    """Pick the most relevant sub-error of an ``anyOf``/``oneOf`` failure.

    Type-aware: when the instance's JSON type matches exactly ONE branch's declared
    ``type``, that branch is the author's evident intent — surface its most relevant
    error (so an object failing ``oneOf[object, string]`` reports the object branch's
    missing field, not the string branch's type mismatch). Otherwise defer to
    jsonschema's ``best_match`` heuristic over all branches.
    """
    context = list(error.context or [])
    if not context:
        return None
    subschemas = error.validator_value
    inst_type = _json_type(error.instance)
    compatible: set[int] = set()
    if isinstance(subschemas, list):
        for idx, sub in enumerate(subschemas):
            if not isinstance(sub, dict):
                continue
            declared = sub.get("type")
            if declared is None or _type_matches(inst_type, declared):
                compatible.add(idx)
    if len(compatible) == 1:
        (branch,) = tuple(compatible)
        branch_errors = [c for c in context if _branch_index(c) == branch]
        if branch_errors:
            return best_match(branch_errors)
    return best_match(context)


def _resolve_effective(error: Any) -> Any:
    """Descend ``anyOf``/``oneOf`` failures to their most relevant leaf sub-error.

    Both the reported path and message come from the returned error, so a nested
    composite failure lands on the concrete offending field. Bounded to guard against
    pathological self-referential schemas.
    """
    current = error
    for _ in range(8):
        if current.validator not in ("anyOf", "oneOf") or not current.context:
            break
        nxt = _select_composite_error(current)
        if nxt is None or nxt is current:
            break
        current = nxt
    return current


_REQUIRED_MSG = re.compile(r"^'([^']+)' is a required property")


def _missing_required(error: Any) -> str | None:
    # jsonschema yields ONE error per missing property, each with its name in the
    # message; extract that so multiple missing fields don't all collapse to the
    # first entry of the schema's ``required`` list.
    match = _REQUIRED_MSG.match(str(error.message))
    if match:
        return match.group(1)
    instance = error.instance
    if isinstance(instance, dict):
        for name in error.validator_value or ():
            if name not in instance:
                return name
    return None


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def format_schema_errors(errors: list[Any]) -> list[Diagnostic]:
    """Turn raw jsonschema errors into deterministic, ordered neutral diagnostics.

    Each error is first resolved through :func:`_resolve_effective` so a composite
    (``anyOf``/``oneOf``) failure is reported against its concrete branch leaf.
    """
    diags = []
    for e in errors:
        eff = _resolve_effective(e)
        schema = eff.schema if isinstance(eff.schema, dict) else {}
        description = schema.get("description")
        if eff.validator == "required" and isinstance(eff.schema, dict):
            missing = _missing_required(eff)
            properties = eff.schema.get("properties")
            field_schema = properties.get(missing) if isinstance(properties, dict) else None
            if isinstance(field_schema, dict):
                description = field_schema.get("description", description)
        try:
            actual = json.dumps(eff.instance, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            actual = repr(eff.instance)
        if len(actual) > 180:
            actual = actual[:88] + "…[bounded]…" + actual[-80:]
        expected = repr(eff.validator_value)
        diags.append(
            Diagnostic(
                path=_json_path(eff),
                message=_describe(eff),
                keyword=str(eff.validator or ""),
                expected=expected,
                actual=actual,
                description=(
                    " ".join(description.split())
                    if isinstance(description, str) and description.strip()
                    else ""
                ),
            )
        )
    diags.sort(key=lambda d: (d.path, d.message))
    return diags


# ─────────────────────────────────────────────────────────────────────────────
# Gate functions — return NEUTRAL diagnostics (no level baked in)
# ─────────────────────────────────────────────────────────────────────────────


def json_schema_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Structural gate: validate the output against the agent's resolved JSON Schema.

    The runner pre-loads the schema onto the request, so this gate never resolves a
    path itself. Errors are formatted deterministically.
    """
    if request.schema is None:
        return []
    validator = compile_validator(request.schema, request.schema_dir)
    return format_schema_errors(list(validator.iter_errors(request.output)))


def _finding_array_fields(request: GateRequest) -> tuple[str, str, str]:
    """Resolve ``(findings_field, item_array_field, path_field)`` for a grounding gate.

    Example: ``findings[].anchors[].filePath`` → ``("findings", "anchors", "filePath")``,
    so a config whose finding array is ``anchors`` instead of ``locations`` reuses this
    one function without a per-agent param.
    """
    findings, items, path = request.field_names(
        slots=("findings", "locations", "path"),
        defaults=("findings", "locations", "filePath"),
    )
    return findings, items, path


def grounded_locations_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Context gate: every finding anchor must reference a changed file.

    Needs external context (``changed_files``), so it cannot live in the schema.
    Neutral: emits one diagnostic per ungrounded anchor; the runner stamps the level.
    When no ``changed_files`` context is present, grounding is unverifiable and the gate
    is a no-op. Field-name-agnostic: the finding array (``locations``/``anchors``) and
    its path field are resolved from the gate's ``requires`` (see
    :func:`_finding_array_fields`), so any config reuses it via its own ``gates.yaml``.

    Why this one gate stays in the engine while its siblings moved into their bundles:
    it reads no config vocabulary at all. Both bundles wire it, spelling the array
    ``locations`` and ``anchors`` respectively, and it works unchanged — because the
    only thing it asserts is "a cited path is among the run's changed files", which is
    a property of the *run*, not of any review contract. Duplicating it into two
    bundles would fork one behavior; promoting it to ``gates_core.yaml`` would make it
    implicit. It stays here as a batteries-available primitive each bundle opts into
    by name.
    """
    changed_files = set(request.context.get("changed_files") or [])
    if not changed_files:
        return []
    findings_field, array_field, path_field = _finding_array_fields(request)
    diags: list[Diagnostic] = []
    for i, finding in request.findings(findings_field):
        locs = finding.get(array_field)
        for j, loc in enumerate(locs if isinstance(locs, list) else []):
            if not isinstance(loc, dict):
                continue
            fp = loc.get(path_field)
            if not isinstance(fp, str) or not fp:
                continue
            file_part = fp.split(":")[0].split("#")[0]
            if file_part and not _path_in_changed_files(file_part, changed_files):
                diags.append(
                    Diagnostic(
                        path=f"{findings_field}[{i}].{array_field}[{j}].{path_field}",
                        message=f"location {fp!r} is not among the changed files (ungrounded)",
                    )
                )
    return diags


# ── No default generic-fix blocklist ─────────────────────────────────────────
# A closed phrase list can never be complete and is gameable, so it does NOT
# belong on an ERROR-level gate: `fix_present` proves only what it can decide
# deterministically and completely (a fix is present, non-empty, and well-formed).
# Boilerplate/quality is a semantic judgment owned by the agent prompt + rubric
# and surfaced advisory-only by the sibling `generic_phrase` gate (warn).


# ─── fn name → callable dispatch (CODE, not contract data) ───────────────────
# Only the task-agnostic primitives live here. Every gate that encodes a particular
# review contract belongs to the bundle that declares it and arrives through
# :func:`register_gate_function` — so this table is a floor, not a catalogue.
_GATE_FUNCTIONS: dict[str, GateFn] = {
    "json_schema": json_schema_gate,
    "grounded_locations": grounded_locations_gate,
}


def register_gate_function(name: str, fn: GateFn) -> None:
    """Register a gate callable so ``gates.yaml`` can reference it by ``fn`` name.

    The open half of the open-closed seam: a config bundle registers its own domain
    gates from its ``plugins:`` module, so the engine never imports a bundle. Rejects a
    duplicate name rather than silently overwriting — two bundles claiming one ``fn``
    name is a configuration bug, not a last-writer-wins merge (mirrors
    ``context.enrichers.register_enricher``).
    """
    existing = _GATE_FUNCTIONS.get(name)
    if existing is not None and existing is not fn:
        raise SchemaLoadError(f"gate fn {name!r} is already registered by {existing.__module__!r}")
    _GATE_FUNCTIONS[name] = fn


# ─────────────────────────────────────────────────────────────────────────────
# Registry + hint loading
# ─────────────────────────────────────────────────────────────────────────────

_VALID_LEVELS = ("error", "warn")


def load_gate_registry(path: Path | None = None) -> dict[str, GateSpec]:
    """Compose the gate registry: engine-core universal gates ∪ this bundle's domain gates.

    Universal, task-agnostic gates (``json_schema``, …) come from the engine-core
    manifest (``validation/gates_core.yaml``); domain gates come from the selected
    bundle (``configs/<name>/gates.yaml``, or an explicit ``path``). A bundle may NOT
    redefine a universal gate name — a collision is rejected. Fails loud on an unknown
    ``fn`` or an invalid ``default_level`` — a manifest bug must never silently
    disable a gate.
    """
    core = _registry_from_manifest(_CORE_GATES_MANIFEST)
    domain_path = path or _gates_manifest()
    domain = _registry_from_manifest(domain_path, allow_empty=True) if domain_path.is_file() else {}
    clash = core.keys() & domain.keys()
    if clash:
        raise SchemaLoadError(f"bundle gates redefine universal gate(s): {sorted(clash)}")
    return {**core, **domain}


def _registry_from_manifest(src: Path, allow_empty: bool = False) -> dict[str, GateSpec]:
    """Parse one gate manifest file into ``{name: GateSpec}`` (no composition).

    ``allow_empty`` is set for a bundle's DOMAIN manifest: a config that wires only
    engine-core universal gates (e.g. ``json_schema``) has no domain gates, so an empty
    ``gates:`` mapping is valid there and yields ``{}``. The engine-core manifest is
    always loaded strictly (``allow_empty=False``) — an empty core manifest is a bug.
    """
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    gates = data.get("gates") if isinstance(data, dict) else None
    if not isinstance(gates, dict) or not gates:
        if allow_empty and (gates == {} or gates is None):
            return {}
        raise SchemaLoadError(f"gates.yaml: missing or empty 'gates' mapping ({src})")

    registry: dict[str, GateSpec] = {}
    for name, rec in gates.items():
        rec = rec or {}
        fn_name = rec.get("fn", name)
        fn = _GATE_FUNCTIONS.get(fn_name)
        if fn is None:
            raise SchemaLoadError(f"gate {name!r}: unknown fn {fn_name!r}")
        level = rec.get("default_level", "error")
        if level not in _VALID_LEVELS:
            raise SchemaLoadError(f"gate {name!r}: invalid default_level {level!r}")
        registry[name] = GateSpec(
            name=name,
            fn=fn,
            default_level=level,
            default_hint=rec.get("default_hint"),
            requires=tuple(rec.get("requires", ()) or ()),
            accepts_arbitrary_json_root=bool(rec.get("accepts_arbitrary_json_root", False)),
        )
    return registry


def load_hint(name: str, hint_dir: Path | None = None) -> str:
    """Load a named corrective-feedback hint.

    Resolves the bundle's ``hints/`` dir first (``configs/inspectorx/hints/<name>.md``),
    then falls back to the engine-core universal hints (``validation/hints_core/``) so a
    universal gate's feedback (e.g. ``json_schema``) is available to any config.
    """
    for base in (hint_dir or _hint_dir(), _CORE_HINT_DIR):
        base = base.resolve()
        target = (base / f"{name}.md").resolve()
        if base != target and base not in target.parents:
            raise SchemaLoadError(f"hint name escapes hint dir: {name!r}")
        if target.is_file():
            return target.read_text(encoding="utf-8")
    raise SchemaLoadError(f"hint not found: {name!r} ({name}.md in bundle or engine-core)")

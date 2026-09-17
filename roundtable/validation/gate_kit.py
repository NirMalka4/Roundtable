"""gate_kit: the public, config-agnostic helper surface for OVG gate authors.

A gate callable does not have to live in this package. A config bundle owns the gates
that encode ITS review contract and registers them by name
(:func:`roundtable.validation.gates.register_gate_function`); the engine never
enumerates them. This module is what those out-of-tree gates are allowed to depend
on — a stable, importable leaf with no knowledge of any config.

Two ideas carry most of the weight:

**Neutral diagnostics.** A gate returns :class:`Diagnostic` objects that carry a
location and a message and *no severity*. The runner stamps ``error``/``warn`` from
the per-agent wiring, so the same callable serves a blocking contract in one config
and an advisory in another.

**Field names come from ``requires``, not from Python.** A gate's declared
``requires`` field-paths in ``gates.yaml`` are the SSOT the doctor already walks
against each agent's ``output_schema``. :func:`resolve_field_names` reads the field
names back out of that same list, so a runtime read agrees with the doctor's
coherence check *by construction*: a wrong path fails the doctor loudly instead of
silently no-opping at review time. This is what lets one callable serve two configs
that spell the same concept differently (``locations`` vs ``anchors``).

This module imports nothing from ``roundtable.configs`` and must stay that way — the
dependency arrow points from a bundle *into* here, never out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from roundtable.graph import Configuration

from ._jscompat import MISSING, is_js_integer, is_js_number, js_str, js_typeof, nullish

__all__ = [
    "MISSING",
    "Diagnostic",
    "GateFn",
    "GateRequest",
    "absent",
    "is_js_integer",
    "is_js_number",
    "iter_findings",
    "js_str",
    "js_typeof",
    "nonempty_str",
    "norm_path",
    "nullish",
    "parse_requires",
    "path_in_changed_files",
    "resolve_field_names",
]


@dataclass(frozen=True)
class Diagnostic:
    """A single, severity-NEUTRAL gate finding.

    ``path`` is a JSON location (e.g. ``findings[2].locations`` or ``""`` for the
    document root); ``message`` is a deterministic, project-owned description. The
    level (error/warn) is applied by the runner from the gate's config, never here.
    """

    path: str
    message: str
    keyword: str = ""
    expected: str = ""
    actual: str = ""
    description: str = ""


class GateFn(Protocol):
    """The callable contract every registered gate satisfies.

    One typed argument in, neutral diagnostics out. Keeping the input a single
    :class:`GateRequest` — rather than a positional triple plus ``**params: Any`` — is
    what lets a gate live outside the engine: the envelope is the whole interface, so
    a bundle's gate is type-checkable against it and the runner never needs to know
    which gate wants which keyword.
    """

    def __call__(self, request: GateRequest, /) -> list[Diagnostic]: ...


@dataclass(frozen=True)
class GateRequest:
    """Everything one gate invocation is given, as one typed envelope.

    ``output`` is the agent's parsed JSON object and ``context`` the run-supplied
    grounding data (``changed_files``, ``tool_calls``, the upstream finding-id
    universe, …) — a gate that needs external truth reads it here, which is precisely
    what a JSON Schema cannot express.

    ``requires`` and ``params`` come from the gate's ``gates.yaml`` record: ``requires``
    is the field-path SSOT the doctor walks against the agent's schema (read it back
    via :meth:`field_names`, never hard-code a field name), and ``params`` is the
    optional declarative payload plus any per-agent override.

    ``schema`` is the agent's resolved output schema, pre-loaded by the runner so no
    gate has to know how schemas are located; it is ``None`` for a format-gate-only
    agent.
    """

    agent_id: str
    output: Any
    context: Mapping[str, Any] = field(default_factory=dict)
    requires: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] | None = None
    schema_dir: Path | None = None
    configuration: Configuration | None = None

    def field_names(self, slots: Sequence[str], defaults: Sequence[str]) -> tuple[str, ...]:
        """This request's field names for ``slots``, derived from ``requires``.

        Thin bind of :func:`resolve_field_names` to the envelope — the form a gate
        body should use, so the read is anchored to the declared paths by construction.
        """
        return resolve_field_names(self.requires, self.params, slots, defaults)

    def findings(self, field_name: str = "findings") -> list[tuple[int, dict[str, Any]]]:
        """This request's well-formed findings as ``(index, finding)``."""
        if not isinstance(self.output, Mapping):
            return []
        return iter_findings(self.output, field_name)


# ─── requires-path derivation (the field-name SSOT) ──────────────────────────


def parse_requires(requires: Any) -> tuple[tuple[str, ...], ...]:
    """Split every declared ``requires`` field-path into its segments.

    ``findings[].anchors[].filePath`` → ``("findings", "anchors", "filePath")``. Array
    markers are stripped; the shape of the path, not its arity, is what callers read.
    """
    return tuple(
        tuple(seg for seg in str(r).replace("[]", "").split(".") if seg) for r in (requires or ())
    )


def resolve_field_names(
    requires: Any,
    params: Mapping[str, Any],
    slots: Sequence[str],
    defaults: Sequence[str],
) -> tuple[str, ...]:
    """Map one ``requires`` path onto ordered, named field slots.

    Selects the declared path with exactly ``len(slots)`` segments (falling back to the
    first longer path, truncated, then to ``defaults``), and lets an explicit
    ``params['<slot>_field']`` override any slot. ``slots`` names the override keys;
    ``defaults`` supplies the value when a config declares nothing.

    Example — ``slots=("findings", "locations", "path")`` against
    ``["findings[].anchors[].filePath"]`` yields ``("findings", "anchors",
    "filePath")``, so a gate reads the config's own field names with no Python
    knowledge of them.
    """
    paths = parse_requires(requires)
    n = len(slots)
    match = next(
        (p for p in paths if len(p) == n),
        next((p for p in paths if len(p) > n), ()),
    )
    return tuple(
        params.get(
            f"{slot}_field",
            match[i] if i < len(match) else defaults[i],
        )
        for i, slot in enumerate(slots)
    )


# ─── output traversal ────────────────────────────────────────────────────────


def iter_findings(
    output: Mapping[str, Any], field_name: str = "findings"
) -> list[tuple[int, dict[str, Any]]]:
    """Every well-formed finding in ``output[field_name]`` as ``(index, finding)``.

    Non-list containers and non-object items are skipped rather than reported — item
    *shape* is the ``json_schema`` gate's job, so a domain gate never double-reports it.
    """
    findings = output.get(field_name)
    if not isinstance(findings, list):
        return []
    return [(i, f) for i, f in enumerate(findings) if isinstance(f, dict)]


def absent(value: Any) -> bool:
    """A field is unset when it is missing or explicitly null."""
    return value is MISSING or value is None


def nonempty_str(value: Any) -> bool:
    """True for a string with at least one non-whitespace character."""
    return isinstance(value, str) and bool(value.strip())


# ─── path comparison ─────────────────────────────────────────────────────────


def norm_path(p: str) -> str:
    """Normalize a repo-relative path for comparison (separators + ``./`` prefix)."""
    return p.replace("\\", "/").removeprefix("./")


def path_in_changed_files(file_part: str, changed_files: set[str]) -> bool:
    """Segment-boundary match: exact, or one side a trailing path-segment suffix.

    Agents cite paths at whatever depth they read them, and a diff lists them at
    another; a plain substring test would match ``a/foo.ts`` against ``b/bar_foo.ts``.
    """
    fp = norm_path(file_part)
    for cf in changed_files:
        c = norm_path(cf)
        if fp == c or c.endswith("/" + fp) or fp.endswith("/" + c):
            return True
    return False

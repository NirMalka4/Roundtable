"""pipeline: compute an agent's OVG verdict from the DECLARATIVE gate config.

This is the runtime output-validation engine: it runs the agent's ``ovg_gates``
(declared in ``agent_graph.yaml``) using the reusable registry in :mod:`gates`,
stamping each gate's configured level onto the neutral diagnostics the gate returns.
The structural ``json_schema`` gate validates the output against the agent's
``output_schema``.

:func:`evaluate_agent_output` is the entry point, called per agent by
``engine/agent_runner.run_agent_with_ovg``. See ``AGENTS.md`` (repo root) for
the end-to-end contract and extension runbooks.

Level ownership (single owner): a gate function returns NEUTRAL ``Diagnostic``s; the
level (``error``/``warn``) is applied HERE from the per-agent override or the
registry default. ``error`` diagnostics block (drive retry); ``warn`` diagnostics are
advisory.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..feedback import Deviation, Feedback
from . import gates as _gates
from .gate_kit import GateRequest
from .gates import GateSpec
from .json_format import hypothetical_closure, parse_json_value, validate_json_format

if TYPE_CHECKING:
    from roundtable.graph import Configuration


@dataclass
class LeveledDiagnostic:
    """A gate diagnostic with its stamped level + originating gate."""

    gate: str
    level: str  # 'error' | 'warn'
    path: str
    message: str
    keyword: str = ""
    expected: str = ""
    actual: str = ""
    description: str = ""


@dataclass
class PipelineResult:
    """Verdict of the declarative gate pipeline for one agent output."""

    passed: bool  # no error-level diagnostics (format + gates)
    parsed: Any | None
    errors: list[LeveledDiagnostic] = field(default_factory=list)
    warnings: list[LeveledDiagnostic] = field(default_factory=list)
    failed_gates: tuple[str, ...] = ()  # gates that produced ≥1 error-level diagnostic
    failed_hints: tuple[str, ...] = ()  # ordered-unique hint names for the failed gates

    @property
    def gate(self) -> str:
        """A single representative gate label for telemetry / the runner's fallback.

        ``"all"`` when the output passed; otherwise the first error-producing gate in
        run order (``"format"`` for an unparseable payload — the runner's only
        raw-preservable case). The label is the concrete gate that rejected.
        """
        if self.passed:
            return "all"
        return self.failed_gates[0] if self.failed_gates else "all"

    def error_messages(self) -> list[str]:
        """Error diagnostics as human/agent-readable strings (retry feedback + trace)."""
        return [_format_diag(d) for d in self.errors]

    def warning_messages(self) -> list[str]:
        """Warning diagnostics as strings (recorded, non-blocking)."""
        return [_format_diag(d) for d in self.warnings]

    def to_feedback(self, target: str) -> Feedback:
        """Project this verdict onto the engine-core :class:`Feedback` contract.

        Maps every leveled diagnostic (errors then warnings, in run order) to a
        :class:`Deviation` addressed to ``target`` — the same typed unit a future
        macro re-activation loop consumes. Pure projection; changes no behavior.
        """
        deviations = tuple(
            Deviation(source=d.gate, level=d.level, path=d.path, message=d.message)
            for d in (*self.errors, *self.warnings)
        )
        return Feedback(target=target, deviations=deviations)


def _format_diag(d: LeveledDiagnostic) -> str:
    loc = f"{d.path}: " if d.path else ""
    return f"[{d.gate}] {loc}{d.message}"


def _resolve_level(spec: GateSpec, override: dict[str, Any]) -> str:
    lvl = override.get("level", spec.default_level)
    if lvl not in ("error", "warn"):
        raise _gates.SchemaLoadError(f"gate {spec.name!r}: invalid level override {lvl!r}")
    return lvl


def _resolve_gate_schema(
    schema_param: Any,
    output_schema: dict[str, Any] | None,
    schema_dir: Path | None,
) -> dict[str, Any] | None:
    """Resolve the schema in effect for one gate entry.

    A gate may point at a named companion schema declaratively via ``params.schema``
    (e.g. a warn-level recommended-field check wired alongside the agent's blocking
    ``output_schema``); otherwise the agent's own ``output_schema`` applies. Resolved
    for EVERY gate so the runner never special-cases a particular callable — a
    schema-unaware gate simply ignores the field.
    """
    if isinstance(schema_param, str):
        return _gates.load_schema_document(schema_param, schema_dir)
    return schema_param if schema_param is not None else output_schema


def _json_root_type(value: Any) -> str:
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
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def run_pipeline(
    agent_id: str,
    raw: str,
    context: dict[str, Any] | None,
    *,
    output_schema: dict[str, Any] | None,
    ovg_gates: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    registry: dict[str, GateSpec],
    schema_dir: Path | None = None,
    configuration: Configuration | None = None,
) -> PipelineResult:
    """Run the declarative gate pipeline. Returns a leveled verdict.

    ``output_schema`` is the resolved schema dict (or None for a format-gate-only
    agent). ``ovg_gates`` is the agent's ordered gate list; each item is a mapping
    ``{gate: <name>, level?: ..., hint?: ...}``. Gates are looked up in ``registry``.
    """
    fmt = parse_json_value(raw)
    if not fmt.passed:
        format_errors = [LeveledDiagnostic("format", "error", "", m) for m in fmt.errors]
        hypothetical = hypothetical_closure(raw)
        if hypothetical is not None:
            follow_on = run_parsed_pipeline(
                agent_id,
                hypothetical,
                context,
                output_schema=output_schema,
                ovg_gates=ovg_gates,
                registry=registry,
                schema_dir=schema_dir,
                configuration=configuration,
            )
            format_errors.extend(
                LeveledDiagnostic(
                    d.gate,
                    d.level,
                    d.path,
                    "Diagnostic-only closure also reveals: " + d.message,
                    d.keyword,
                    d.expected,
                    d.actual,
                    d.description,
                )
                for d in follow_on.errors
            )
        return PipelineResult(
            passed=False,
            parsed=None,
            errors=format_errors,
            failed_gates=("format",),
        )

    return run_parsed_pipeline(
        agent_id,
        fmt.parsed,
        context,
        output_schema=output_schema,
        ovg_gates=ovg_gates,
        registry=registry,
        schema_dir=schema_dir,
        configuration=configuration,
    )


def run_parsed_pipeline(
    agent_id: str,
    parsed: Any,
    context: dict[str, Any] | None,
    *,
    output_schema: dict[str, Any] | None,
    ovg_gates: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    registry: dict[str, GateSpec],
    schema_dir: Path | None = None,
    configuration: Configuration | None = None,
) -> PipelineResult:
    """Run the complete ordered gate pipeline on an already-parsed JSON value."""

    ctx = context or {}

    errors: list[LeveledDiagnostic] = []
    warnings: list[LeveledDiagnostic] = []
    failed: list[str] = []
    failed_hints: list[str] = []

    for entry in ovg_gates or ():
        name = entry.get("gate")
        if not name:
            raise _gates.SchemaLoadError(f"{agent_id}: gate entry missing a 'gate' key: {entry!r}")
        spec = registry.get(name)
        if spec is None:
            raise _gates.SchemaLoadError(f"{agent_id}: unknown gate {name!r} (not in registry)")
        level = _resolve_level(spec, entry)

        params = dict(entry.get("params") or {})
        request = GateRequest(
            agent_id=agent_id,
            output=parsed,
            context=ctx,
            # The gate's declared `requires` paths, so field-name-agnostic gates derive
            # which finding array/field to read from the SAME SSOT the doctor validates
            # — no per-agent field param, no drift.
            requires=spec.requires,
            params=params,
            schema=_resolve_gate_schema(params.pop("schema", None), output_schema, schema_dir),
            schema_dir=schema_dir,
            configuration=configuration,
        )

        if not spec.accepts_arbitrary_json_root and not isinstance(parsed, Mapping):
            actual_type = _json_root_type(parsed)
            diags = [
                _gates.Diagnostic(
                    "",
                    f"gate requires a JSON object root, got {actual_type}",
                    keyword="type",
                    expected="object",
                    actual=actual_type,
                )
            ]
        else:
            diags = spec.fn(request)
        if not diags:
            continue
        bucket = errors if level == "error" else warnings
        bucket.extend(
            LeveledDiagnostic(
                spec.name,
                level,
                d.path,
                d.message,
                d.keyword,
                d.expected,
                d.actual,
                d.description,
            )
            for d in diags
        )
        if level == "error":
            failed.append(spec.name)
            hint_name = entry.get("hint") or spec.default_hint
            if hint_name and hint_name not in failed_hints:
                failed_hints.append(hint_name)

    return PipelineResult(
        passed=len(errors) == 0,
        parsed=parsed,
        errors=errors,
        warnings=warnings,
        failed_gates=tuple(failed),
        failed_hints=tuple(failed_hints),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runtime entry point — resolve the agent's declarative contract from the graph
# ─────────────────────────────────────────────────────────────────────────────


@cache
def _registry(root: Path) -> dict[str, GateSpec]:
    return _gates.load_gate_registry(root / "gates.yaml")


@cache
def _schema_for(relpath: str, root: Path) -> dict[str, Any]:
    return _gates.load_schema_document(relpath, root / "schemas")


def _parse_only_result(raw: str, extra_warnings: list[LeveledDiagnostic]) -> PipelineResult:
    """Parse-only verdict for an agent with no declarative gate contract."""
    fmt = validate_json_format(raw)
    if not fmt.passed or not isinstance(fmt.parsed, dict):
        return PipelineResult(
            passed=False,
            parsed=None,
            errors=[LeveledDiagnostic("format", "error", "", m) for m in fmt.errors]
            or [LeveledDiagnostic("format", "error", "", "Root must be a JSON object")],
            warnings=extra_warnings,
            failed_gates=("format",),
        )
    return PipelineResult(passed=True, parsed=fmt.parsed, warnings=extra_warnings)


def evaluate_agent_output(
    agent_id: str,
    raw: str,
    context: dict[str, Any] | None = None,
    *,
    configuration: Configuration | None = None,
) -> PipelineResult:
    """Runtime OVG entry: run the agent's declarative gate pipeline on ``raw`` output.

    Resolves the agent's ``output_schema`` + ``ovg_gates`` from the graph config
    (keyed by ``GraphEntry.key``) and runs :func:`run_pipeline` with the shared gate
    registry.

    Two contract-less cases degrade to parse-only: an agent **absent** from the graph
    is surfaced once, loudly (``unrecognized agent`` — the D-0001 registry-aware
    guard, since a genuinely unknown agent has no contract to enforce); a **known**
    in-graph agent with no ``output_schema`` passes silently (a declared schemaless
    agent, e.g. non-finding infra).
    """
    import sys

    from roundtable.graph import get_configuration

    config = configuration or get_configuration()
    root = config.root
    entry = config.by_key.get(agent_id)
    if entry is None:
        warning = LeveledDiagnostic(
            "agent", "warn", "", f"unrecognized agent {agent_id!r} — no schema/gate contract"
        )
        print(_format_diag(warning), file=sys.stderr)
        return _parse_only_result(raw, [warning])
    if entry.output_schema is None:
        return _parse_only_result(raw, [])

    return run_pipeline(
        agent_id,
        raw,
        context,
        output_schema=_schema_for(entry.output_schema, root),
        ovg_gates=entry.ovg_gates,
        registry=_registry(root),
        schema_dir=root / "schemas",
        configuration=config,
    )


def evaluate_agent_value(
    agent_id: str,
    value: Any,
    context: dict[str, Any] | None = None,
    *,
    configuration: Configuration | None = None,
) -> PipelineResult:
    """Runtime OVG entry for a value already parsed by a backend tool call."""

    from roundtable.graph import get_configuration

    config = configuration or get_configuration()
    entry = config.by_key.get(agent_id)
    if entry is None or entry.output_schema is None:
        return PipelineResult(
            passed=False,
            parsed=value,
            errors=[
                LeveledDiagnostic(
                    "submission",
                    "error",
                    "",
                    f"agent {agent_id!r} has no schema-backed submission contract",
                )
            ],
            failed_gates=("submission",),
        )
    return run_parsed_pipeline(
        agent_id,
        value,
        context,
        output_schema=_schema_for(entry.output_schema, config.root),
        ovg_gates=entry.ovg_gates,
        registry=_registry(config.root),
        schema_dir=config.root / "schemas",
        configuration=config,
    )

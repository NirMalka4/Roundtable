"""Generic, config-driven schema↔gate coherence checks for the ``doctor``.

This is the static guardrail the OVG consolidation was built for: it catches the
*structural* silent bugs that arise when an agent's declarative output contract
(``output_schema``) and its wired ``ovg_gates`` drift apart — e.g. wiring a gate that
reads fields the schema never emits (a dead gate), or declaring a shape that a gate is
meant to guard without wiring that gate.

Design (per the agreed plan — no contract constants in code):

  * ZERO agent or field names live here. Every rule is driven by each gate's declared
    ``requires`` field-paths (``configs/inspectorx/gates.yaml``) walked generically against each
    agent's resolved schema. A new agent/schema/gate is covered with NO edit here.
  * Paths are compared as **property-name sequences** (array ``[]`` markers dropped),
    so ``findings[].locations[].filePath`` and the schema's ``findings → locations``
    declaration line up regardless of whether the schema describes the array internals
    (which, by the gate-owns-the-floor partition, it deliberately may not).

Two generic rules:

  1. **Applicability** (ERROR, gate→schema): a *wired* gate whose ``requires`` roots are
     entirely absent from the schema can never fire meaningfully — a dead wiring.
  2. **Coverage** (WARN, schema→gate): a gate that is *not* wired but whose ``requires``
     trigger-paths are fully declared by the schema is probably missing — advisory only
     (some coverage is semantic and cannot be inferred from shape; those stay author
     responsibility and can be silenced with an explicit waiver).

Plus reference/authoring integrity (all ERROR): every ``is_llm`` agent is wired; each
schema loads + compiles; its ``examples[0]`` validates against itself **and passes its own
context-free error gates** (so the example is the agent's happy-path output modulo runtime
context — the invariant ``--simulate`` relies on); every wired gate name + effective hint
resolves.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roundtable import bundle as paths

from .gates import (
    GateSpec,
    SchemaLoadError,
    compile_validator,
    load_gate_registry,
    load_hint,
    load_schema_document,
)
from .pipeline import run_pipeline
from .submission_schema import build_submission_schema

# Minimum schema-declared depth at which a gate's ``requires`` path counts as a
# coverage trigger. Depth 1 (the root collection alone, e.g. ``findings``) is too
# weak — nearly every finding agent has ``findings`` — so coverage keys off the
# second segment (e.g. ``findings → locations``), the shape a gate actually guards.
_COVERAGE_DEPTH = 2


@dataclass
class CoherenceReport:
    """Collected coherence findings. ``errors`` fail the doctor; ``warnings`` advise."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: CoherenceReport) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def _requires_sequence(path: str) -> tuple[str, ...]:
    """``findings[].locations[].filePath`` → ``('findings', 'locations', 'filePath')``."""
    return tuple(seg[:-2] if seg.endswith("[]") else seg for seg in path.split(".") if seg)


def schema_property_sequences(
    schema: dict[str, Any], schema_dir: Path | None = None
) -> set[tuple[str, ...]]:
    """Every property-name sequence the schema can produce (array markers dropped).

    ``required`` names count as declared even without a ``properties`` subschema — the
    schema states the field exists (the gate-owned floor may describe its internals).
    """
    out: set[tuple[str, ...]] = set()
    _walk_schema(schema, (), out, (schema_dir or paths.schema_dir()), set())
    return out


def _walk_schema(
    node: Any,
    prefix: tuple[str, ...],
    out: set[tuple[str, ...]],
    schema_dir: Path,
    seen_refs: set[str],
) -> None:
    if not isinstance(node, dict):
        return

    ref = node.get("$ref")
    if isinstance(ref, str) and "://" not in ref:
        relpath = ref.split("#", 1)[0]
        if relpath and relpath not in seen_refs:
            seen_refs.add(relpath)
            # reference integrity is reported separately by compile_validator
            with contextlib.suppress(SchemaLoadError):
                _walk_schema(
                    load_schema_document(relpath, schema_dir), prefix, out, schema_dir, seen_refs
                )

    for combiner in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(combiner) or []:
            _walk_schema(sub, prefix, out, schema_dir, seen_refs)

    props = node.get("properties")
    props = props if isinstance(props, dict) else {}
    declared = set(props.keys())
    required = node.get("required")
    if isinstance(required, list):
        declared |= {r for r in required if isinstance(r, str)}

    for name in declared:
        seq = (*prefix, name)
        out.add(seq)
        sub = props.get(name)
        if isinstance(sub, dict):
            _walk_schema(sub, seq, out, schema_dir, seen_refs)

    items = node.get("items")
    if isinstance(items, dict):
        _walk_schema(items, prefix, out, schema_dir, seen_refs)  # array element shares the path


def _reachable(seq: tuple[str, ...], schema_seqs: set[tuple[str, ...]], min_depth: int) -> bool:
    """Is the ``min_depth``-segment prefix of ``seq`` a prefix of some declared sequence?"""
    depth = min(min_depth, len(seq))
    if depth == 0:
        return False
    prefix = seq[:depth]
    return any(len(s) >= depth and s[:depth] == prefix for s in schema_seqs)


def _wired_gate_names(entry: Any) -> list[str]:
    return [
        gate for g in (entry.ovg_gates or ()) if isinstance(g, dict) and (gate := g.get("gate"))
    ]


def _effective_hint(gate_cfg: dict[str, Any], spec: GateSpec) -> str | None:
    hint = gate_cfg.get("hint")
    return hint if isinstance(hint, str) and hint else spec.default_hint


def validate_ovg_coherence(
    graph: Any,
    *,
    configuration: Any,
    registry: dict[str, GateSpec] | None = None,
    schema_dir: Path | None = None,
    hint_dir: Path | None = None,
) -> CoherenceReport:
    """Static schema↔gate coherence for every ``is_llm`` agent in ``graph``.

    Returns a :class:`CoherenceReport`; the caller decides how to surface it (the doctor
    raises on ``errors`` and prints ``warnings``).
    """
    config_root = Path(configuration.root)
    registry = registry if registry is not None else load_gate_registry(config_root / "gates.yaml")
    schema_dir = schema_dir or config_root / "schemas"
    hint_dir = hint_dir or config_root / "hints"
    report = CoherenceReport()

    for entry in graph:
        if not getattr(entry, "is_llm", False):
            continue
        report.extend(_check_agent(entry, registry, schema_dir, hint_dir, configuration))

    return report


def _check_agent(
    entry: Any,
    registry: dict[str, GateSpec],
    schema_dir: Path,
    hint_dir: Path,
    configuration: Any,
) -> CoherenceReport:
    report = CoherenceReport()
    key = entry.key

    if entry.output_schema is None or entry.ovg_gates is None:
        report.errors.append(
            f"{key}: is_llm agent must declare both `output_schema` and `ovg_gates` "
            f"(got output_schema={entry.output_schema!r}, ovg_gates={entry.ovg_gates!r})"
        )
        return report

    try:
        schema = load_schema_document(entry.output_schema, schema_dir)
        compile_validator(schema, schema_dir)  # raises on invalid schema / remote $ref
        build_submission_schema(entry.output_schema, schema_dir)
    except SchemaLoadError as exc:
        report.errors.append(f"{key}: cannot load output_schema {entry.output_schema!r}: {exc}")
        return report
    except Exception as exc:  # jsonschema.SchemaError et al.
        report.errors.append(f"{key}: invalid output_schema {entry.output_schema!r}: {exc}")
        return report

    report.extend(_check_example(key, schema, schema_dir))
    report.extend(
        _check_example_gate_valid(entry, key, schema, registry, schema_dir, configuration)
    )
    report.extend(_check_output_example(entry, key, schema, schema_dir))

    schema_seqs = schema_property_sequences(schema, schema_dir)
    wired = _wired_gate_names(entry)
    report.extend(_check_wired_gates(key, entry, wired, registry, schema_seqs, hint_dir))
    report.extend(_check_coverage(key, wired, registry, schema_seqs))
    return report


def _check_example(key: str, schema: dict[str, Any], schema_dir: Path) -> CoherenceReport:
    report = CoherenceReport()
    examples = schema.get("examples")
    if not isinstance(examples, list) or not examples:
        report.warnings.append(
            f"{key}: schema has no `examples` — the example drives the LLM output-format "
            f"hint and the differential parity corpus"
        )
        return report
    validator = compile_validator(schema, schema_dir)
    errs = sorted(validator.iter_errors(examples[0]), key=lambda e: list(e.absolute_path))
    if errs:
        locs = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errs[:5])
        report.errors.append(
            f"{key}: schema examples[0] does not validate against its own schema: {locs}"
        )
    return report


def _check_example_gate_valid(
    entry: Any,
    key: str,
    schema: dict[str, Any],
    registry: dict[str, GateSpec],
    schema_dir: Path,
    configuration: Any,
) -> CoherenceReport:
    """``examples[0]`` must pass the agent's own *context-free* error gates.

    Schema self-validation (``_check_example``) proves the example's *shape*; it does not
    prove the example satisfies the wired gates that enforce the deeper floor
    (``locations_floor`` / ``proof_depth`` / …). If it doesn't, the example is not the
    agent's happy-path output, and ``--simulate`` — whose mock emits that example — can
    never make the agent valid. This makes "``examples[0]`` is the OVG-valid happy path
    (modulo runtime context)" a statically-enforced invariant rather than an assumption.

    Run through the real runtime pipeline with an **empty context** so context gates
    (``grounded_locations`` — needs the review diff) self-skip by their own no-op-without-
    context contract, and only context-free error gates are asserted. Only ERROR-level
    failures are reported (warn-level gates are advisory by design).
    """
    report = CoherenceReport()
    examples = schema.get("examples")
    if not isinstance(examples, list) or not examples:
        return report  # absence already warned by _check_example
    raw = json.dumps(examples[0])
    try:
        result = run_pipeline(
            key,
            raw,
            {},
            output_schema=schema,
            ovg_gates=entry.ovg_gates,
            registry=registry,
            schema_dir=schema_dir,
            configuration=configuration,
        )
    except SchemaLoadError as exc:
        report.errors.append(f"{key}: cannot evaluate examples[0] against its gates: {exc}")
        return report
    if not result.passed:
        detail = "; ".join(
            f"[{e.gate}] {e.path or '<root>'}: {e.message}" for e in result.errors[:5]
        )
        report.errors.append(
            f"{key}: schema examples[0] fails its own context-free error gate(s) "
            f"{list(result.failed_gates)}: {detail}"
        )
    return report


def _check_output_example(
    entry: Any, key: str, schema: dict[str, Any], schema_dir: Path
) -> CoherenceReport:
    """A declared ``output_example`` must load and validate against ``output_schema``.

    The example is the richer per-agent shape shown to the LLM in place of the shared
    schema's own ``examples[0]``; if it drifts from the schema the agent would be told
    to emit a shape the gate then rejects. This catches that silently."""
    report = CoherenceReport()
    relpath = getattr(entry, "output_example", None)
    if not relpath:
        return report
    try:
        example = load_schema_document(relpath, schema_dir)
    except SchemaLoadError as exc:
        report.errors.append(f"{key}: cannot load output_example {relpath!r}: {exc}")
        return report
    validator = compile_validator(schema, schema_dir)
    errs = sorted(validator.iter_errors(example), key=lambda e: list(e.absolute_path))
    if errs:
        locs = "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errs[:5])
        report.errors.append(
            f"{key}: output_example {relpath!r} does not validate against output_schema: {locs}"
        )
    return report


def _check_wired_gates(
    key: str,
    entry: Any,
    wired: list[str],
    registry: dict[str, GateSpec],
    schema_seqs: set[tuple[str, ...]],
    hint_dir: Path,
) -> CoherenceReport:
    report = CoherenceReport()
    for gate_cfg in entry.ovg_gates:
        name = gate_cfg.get("gate")
        spec = registry.get(name)
        if spec is None:
            report.errors.append(f"{key}: wires unknown gate {name!r} (not in gates.yaml)")
            continue

        hint = _effective_hint(gate_cfg, spec)
        if hint is not None:
            try:
                load_hint(hint, hint_dir)
            except SchemaLoadError:
                report.errors.append(f"{key}: gate {name!r} references missing hint {hint!r}")

        seqs = [_requires_sequence(r) for r in spec.requires]
        if seqs and not any(_reachable(s, schema_seqs, 1) for s in seqs):
            report.errors.append(
                f"{key}: gate {name!r} is dead — none of its `requires` roots "
                f"({', '.join(spec.requires)}) exist in the output_schema"
            )
    return report


def _check_coverage(
    key: str,
    wired: list[str],
    registry: dict[str, GateSpec],
    schema_seqs: set[tuple[str, ...]],
) -> CoherenceReport:
    report = CoherenceReport()
    for name, spec in registry.items():
        if name in wired or not spec.requires:
            continue
        seqs = [_requires_sequence(r) for r in spec.requires]
        if all(_reachable(s, schema_seqs, _COVERAGE_DEPTH) for s in seqs):
            report.warnings.append(
                f"{key}: schema declares the fields gate {name!r} guards "
                f"({', '.join(spec.requires)}) but does not wire it — wire the gate or "
                f"add a waiver if intentional"
            )
    return report


# ---------------------------------------------------------------------------
# Cross-agent output-field reference lint (class-B coupling).
#
# ``validate_ovg_coherence`` above is strictly *intra-agent* — an agent's own
# schema vs its own gates. It never reads ``.agent.md`` prose and never checks a
# *consumer's* reference to a *producer's* output field. So a prompt that says
# ``security_focus_pack.selected_sec_checks`` is unguarded: if the producer is
# removed, the edge is dropped, or the field is renamed, the prompt silently lies.
#
# This check closes that gap generically — ZERO agent/field/label names in code:
#   * Registry: every entry with an ``output_schema`` (LLM *or* deterministic)
#     publishes its top-level schema properties under its normalized
#     ``delivery_label`` (default ``## Context from <Key>``). This also validates
#     each deterministic producer's schema + example, which Layer 5 skips (the
#     F5/F10 gap).
#   * Lint: for each ``is_llm`` consumer, scan its instructions + shared_context
#     files for ``<registered_label>.<field>`` references. A reference is VALID iff
#     (R3) the consumer declares a ``hard_dep`` OR ``soft_dep`` on the producer that
#     owns the label — no edge is an ERROR — AND ``<field>`` is a top-level property
#     of that producer's schema. A producer referencing its own label is exempt.
# ---------------------------------------------------------------------------

#: A member-access reference ``<label>.<field>`` — ``<field>`` is a single
#: identifier segment. The ``<label>`` alternation is injected at build time from
#: the registered delivery labels, so the prefix can only match a real producer
#: (prose like ``config.json`` never matches a registered pack) — near-zero false
#: positives.
_FIELD_SEGMENT = r"([A-Za-z_][A-Za-z0-9_]*)"


def _normalize_label(label: str) -> str:
    """Strip the Markdown heading marker + surrounding whitespace off a
    ``delivery_label`` so ``## security_focus_pack`` matches a prompt's bare
    ``security_focus_pack.<field>`` reference."""
    return label.lstrip("#").strip()


def _reference_registry(
    graph: Any, schema_dir: Path, report: CoherenceReport
) -> dict[str, tuple[str, frozenset[str]]]:
    """Build ``normalized_label -> (producer_key, top_level_schema_props)`` for every
    entry that declares an ``output_schema`` (LLM and deterministic alike).

    Also validates each schema loads/compiles, and (for the deterministic producers
    Layer 5 skips) that its ``examples[0]`` self-validates — closing the F5/F10 gap.
    Load/compile failures are recorded as errors and the producer is omitted.
    """
    registry: dict[str, tuple[str, frozenset[str]]] = {}
    for entry in graph:
        if not entry.output_schema:
            continue
        try:
            schema = load_schema_document(entry.output_schema, schema_dir)
            compile_validator(schema, schema_dir)
        except SchemaLoadError as exc:
            report.errors.append(
                f"{entry.key}: cannot load output_schema {entry.output_schema!r}: {exc}"
            )
            continue
        except Exception as exc:  # jsonschema.SchemaError et al.
            report.errors.append(
                f"{entry.key}: invalid output_schema {entry.output_schema!r}: {exc}"
            )
            continue

        # Deterministic producers are skipped by validate_ovg_coherence (Layer 5),
        # so validate their self-consistency here; is_llm ones are already covered.
        if not getattr(entry, "is_llm", False):
            report.extend(_check_example(entry.key, schema, schema_dir))

        top_props = frozenset(seq[0] for seq in schema_property_sequences(schema, schema_dir))
        label = _normalize_label(entry.delivery_label or f"## Context from {entry.key}")
        registry[label] = (entry.key, top_props)
    return registry


def _consumer_source_files(entry: Any, bundle_root: Path) -> list[tuple[str, str]]:
    """``(relpath, text)`` for a consumer's instructions + shared_context files.

    Missing files are silently skipped — presence is Layer 2's contract. The raw
    file text (frontmatter included) is returned so reported line numbers match the
    file on disk."""
    refs: list[str] = []
    if getattr(entry, "prompt_path", None):
        refs.append(entry.prompt_path)
    refs.extend(entry.shared_context)

    out: list[tuple[str, str]] = []
    for rel in refs:
        try:
            out.append((rel, (bundle_root / rel).read_text(encoding="utf-8")))
        except OSError:
            continue
    return out


def validate_cross_agent_refs(
    graph: Any, bundle_root: Path, *, schema_dir: Path | None = None
) -> CoherenceReport:
    """Lint every ``<delivery_label>.<field>`` reference in agent prompts against the
    producing node's declared ``output_schema`` and the consumer's declared edges.

    A reference is valid iff (R3) the referencing agent declares a ``hard_dep`` or
    ``soft_dep`` on the label's producer AND ``<field>`` is a top-level property of
    that producer's ``output_schema``. A producer referencing its own label is
    exempt. Returns a :class:`CoherenceReport` (all findings are ERRORs)."""
    schema_dir = schema_dir or paths.schema_dir()
    report = CoherenceReport()

    registry = _reference_registry(graph, schema_dir, report)
    if not registry:
        return report

    # Longest-first so a label that is a prefix of another can't shadow it.
    labels = sorted(registry, key=len, reverse=True)
    ref_re = re.compile(
        r"\b(" + "|".join(re.escape(lbl) for lbl in labels) + r")\." + _FIELD_SEGMENT
    )

    for entry in graph:
        if not getattr(entry, "is_llm", False):
            continue
        deps = set(entry.dep_keys)
        for relpath, text in _consumer_source_files(entry, bundle_root):
            for lineno, line in enumerate(text.splitlines(), 1):
                for match in ref_re.finditer(line):
                    label, reffield = match.group(1), match.group(2)
                    producer, props = registry[label]
                    if entry.key == producer:
                        continue  # a producer may reference its own label
                    where = f"{entry.key} ({relpath}:{lineno})"
                    if producer not in deps:
                        report.errors.append(
                            f"{where}: references `{label}.{reffield}` but declares no "
                            f"edge on its producer {producer!r} — add the edge "
                            f"in agent_graph.yaml or drop the reference"
                        )
                        continue
                    if reffield not in props:
                        report.errors.append(
                            f"{where}: references `{label}.{reffield}` but {producer!r}'s "
                            f"output_schema has no top-level field {reffield!r} "
                            f"(declared: {sorted(props)})"
                        )
    return report


# ---------------------------------------------------------------------------
# Conditional-edge predicate coherence (Layer 9).
#
# A conditional edge's ``when:`` reads the output of its ``source`` producer, so
# every leaf field-path must resolve into that producer's ``output_schema``. An
# unresolvable path is always false at runtime — a silent dead branch that would
# skip the consumer on every review. Proven offline here, reusing the same
# schema-property machinery as the cross-agent ref lint (Layer 6).
# ---------------------------------------------------------------------------


def validate_conditional_predicates(
    graph: Any, *, schema_dir: Path | None = None
) -> CoherenceReport:
    """Lint every conditional edge's ``when:`` field-paths against the ``source``
    producer's declared ``output_schema``.

    Two failures, both ERRORs: the producer declares no ``output_schema`` (nothing
    to route on — the predicate can never be evaluated), or a leaf field-path is not
    reachable in that schema (a dead branch that always skips the consumer)."""
    schema_dir = schema_dir or paths.schema_dir()
    report = CoherenceReport()

    by_key = {e.key: e for e in graph}
    seq_cache: dict[str, set[tuple[str, ...]] | None] = {}

    def _producer_sequences(producer: Any) -> set[tuple[str, ...]] | None:
        if producer.key in seq_cache:
            return seq_cache[producer.key]
        try:
            schema = load_schema_document(producer.output_schema, schema_dir)
        except Exception as exc:  # load/parse — also reported by Layer 5/6
            report.errors.append(
                f"cannot load producer {producer.key!r} output_schema "
                f"{producer.output_schema!r}: {exc}"
            )
            seq_cache[producer.key] = None
            return None
        seqs = schema_property_sequences(schema, schema_dir)
        seq_cache[producer.key] = seqs
        return seqs

    for entry in graph:
        for edge in entry.edges:
            if edge.when is None:
                continue
            producer = by_key.get(edge.source)
            if producer is None:
                continue  # missing dep already reported by validate_graph_config
            if not producer.output_schema:
                report.errors.append(
                    f"{entry.key}: conditional edge on {edge.source!r}, but that "
                    f"producer declares no output_schema — a when: predicate cannot "
                    f"be validated or evaluated against a schemaless producer"
                )
                continue
            seqs = _producer_sequences(producer)
            if seqs is None:
                continue
            for path in edge.when.field_paths():
                seq = tuple(p for p in path.split(".") if p)
                if not _reachable(seq, seqs, len(seq)):
                    report.errors.append(
                        f"{entry.key}: when: field {path!r} is not reachable in "
                        f"producer {edge.source!r}'s output_schema "
                        f"(declared top-level: {sorted({s[0] for s in seqs})})"
                    )
    return report


def _is_array_schema(sub: Any, schema_dir: Path, seen_refs: frozenset[str]) -> bool:
    """Does subschema ``sub`` describe an array (directly, via ``$ref``, or combiner)?"""
    if not isinstance(sub, dict):
        return False
    t = sub.get("type")
    if t == "array" or (isinstance(t, list) and "array" in t):
        return True
    if "items" in sub:  # items implies an array even if ``type`` is omitted
        return True
    ref = sub.get("$ref")
    if isinstance(ref, str) and "://" not in ref:
        relpath = ref.split("#", 1)[0]
        if relpath and relpath not in seen_refs:
            with contextlib.suppress(SchemaLoadError):
                return _is_array_schema(
                    load_schema_document(relpath, schema_dir), schema_dir, seen_refs | {relpath}
                )
    for combiner in ("allOf", "anyOf", "oneOf"):
        for s in sub.get(combiner) or []:
            if _is_array_schema(s, schema_dir, seen_refs):
                return True
    return False


def _array_property_sequences(
    schema: dict[str, Any], schema_dir: Path | None = None
) -> set[tuple[str, ...]]:
    """Every property-name sequence whose value is an **array** (a list to fan out over).

    Unlike :func:`schema_property_sequences` (which drops array markers so it can't
    tell an array from a scalar), this records only array-typed field paths — the
    exact shape a ``kind: map`` node needs at ``fan_out.over``."""
    out: set[tuple[str, ...]] = set()
    _walk_arrays(schema, (), out, (schema_dir or paths.schema_dir()), set())
    return out


def _walk_arrays(
    node: Any,
    prefix: tuple[str, ...],
    out: set[tuple[str, ...]],
    schema_dir: Path,
    seen_refs: set[str],
) -> None:
    if not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and "://" not in ref:
        relpath = ref.split("#", 1)[0]
        if relpath and relpath not in seen_refs:
            seen_refs.add(relpath)
            with contextlib.suppress(SchemaLoadError):
                _walk_arrays(
                    load_schema_document(relpath, schema_dir), prefix, out, schema_dir, seen_refs
                )
    for combiner in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(combiner) or []:
            _walk_arrays(sub, prefix, out, schema_dir, seen_refs)
    props = node.get("properties")
    props = props if isinstance(props, dict) else {}
    for name, sub in props.items():
        seq = (*prefix, name)
        if _is_array_schema(sub, schema_dir, frozenset()):
            out.add(seq)
        if isinstance(sub, dict):
            _walk_arrays(sub, seq, out, schema_dir, seen_refs)
    items = node.get("items")
    if isinstance(items, dict):
        _walk_arrays(items, prefix, out, schema_dir, seen_refs)


def validate_fan_out(graph: Any, *, schema_dir: Path | None = None) -> CoherenceReport:
    """Lint every ``kind: map`` node's ``fan_out.over`` against the producer schema.

    Three failures, all ERRORs: the named producer is not a **required** edge source
    of the map node (so the list is never actually delivered), the producer declares
    no ``output_schema`` (nothing to prove the field is a list against), or the field
    at ``fan_out.over`` is not an **array** in that schema (identity fan-out needs a
    list to iterate — a scalar would fan out over nothing)."""
    schema_dir = schema_dir or paths.schema_dir()
    report = CoherenceReport()
    by_key = {e.key: e for e in graph}

    for entry in graph:
        if entry.kind != "map" or entry.fan_out is None:
            continue
        fo = entry.fan_out
        required_sources = {e.source for e in entry.edges if e.required}
        if fo.producer not in required_sources:
            report.errors.append(
                f"{entry.key}: fan_out.over producer {fo.producer!r} is not a required "
                f"edge source of this map node — add a required edge from it so the "
                f"list is delivered before fan-out"
            )
            continue
        producer = by_key.get(fo.producer)
        if producer is None:
            continue  # missing dep already reported by validate_graph_config
        if not producer.output_schema:
            report.errors.append(
                f"{entry.key}: fan_out producer {fo.producer!r} declares no output_schema "
                f"— cannot verify {fo.field_path!r} is a list to fan out over"
            )
            continue
        try:
            schema = load_schema_document(producer.output_schema, schema_dir)
        except Exception as exc:  # load/parse — also reported by Layer 5/6
            report.errors.append(
                f"{entry.key}: cannot load producer {fo.producer!r} output_schema "
                f"{producer.output_schema!r}: {exc}"
            )
            continue
        arrays = _array_property_sequences(schema, schema_dir)
        seq = tuple(p for p in fo.field_path.split(".") if p)
        if seq not in arrays:
            report.errors.append(
                f"{entry.key}: fan_out.over field {fo.field_path!r} is not an array in "
                f"producer {fo.producer!r}'s output_schema (declared arrays: "
                f"{sorted('.'.join(s) for s in arrays)})"
            )
    return report


# ---------------------------------------------------------------------------
# Dossier corpus completeness (Layer 8).
#
# Replaces the safety the deleted ``collects_all`` flag used to provide implicitly:
# a zero-drop consumer's dossier node must aggregate EVERY finding-producing agent,
# so a newly added specialist can never be silently dropped from the reconciled
# corpus. "Finding-producing" is computed from schemas (``x-finding-array``), so the
# invariant self-updates — the rule holds no agent names of its own.
#
# Which nodes are complete-corpus reconcilers is itself DECLARED, not hard-coded:
# a consolidation node marks ``consolidation.zero_drop: true`` in the config
# (Dossier_Judge + Dossier_SeverityInflator do; ExploitEngineer's scoped/additive
# dossier does not). So this check names no agent AND no node of its own — both the
# producer set and the reconciler set come from the config.
# ---------------------------------------------------------------------------


def validate_dossier_completeness(
    graph: Any, *, configuration: Any | None = None
) -> CoherenceReport:
    """Every ``zero_drop`` consolidation node must dep on all finding-producing agents.

    A complete-corpus reconciler is any entry whose ``consolidation.zero_drop`` is set
    (declared in the config, not named here). The corpus is
    ``finding_producing_agent_keys(include_terminal=False)`` — the non-terminal
    producers (terminals SeverityInflator/Judge are excluded to avoid double-counting;
    see the R-C double-count fix). A missing producer is an ERROR: it would silently
    shrink the reconciled universe and defeat zero-drop."""
    from roundtable.graph import finding_producing_agent_keys

    report = CoherenceReport()
    required = set(
        finding_producing_agent_keys(
            include_terminal=False,
            entries=graph,
            config=configuration,
        )
    )

    for entry in graph:
        spec = entry.consolidation
        if spec is None or not spec.zero_drop:
            continue
        scope = set(entry.dep_keys)
        missing = required - scope
        if missing:
            report.errors.append(
                f"{entry.key}: dossier corpus is incomplete — missing finding-producer(s) "
                f"{sorted(missing)}. Add them to its deps in agent_graph.yaml so no "
                f"specialist finding is silently dropped from the reconciled universe."
            )
    return report


# ---------------------------------------------------------------------------
# Extractor ↔ vocabulary coherence (Layer 7).
#
# The extractor's contract is: a finding field named like a canonical term reads
# that term's value, and enum values are trusted because the ``json_schema`` gate
# enforced them. That only holds if the SCHEMAS keep the vocabulary honest. This
# layer proves — statically, with ZERO agent/field/enum names in code — that:
#
#   (a) every vocabulary ``$def`` carries a non-empty ``description`` (the single
#       source ``build_schema_hint`` surfaces to agents);
#   (b) ONE NAME = ONE TYPE: any schema field whose NAME is a canonical ``$def``
#       must ``$ref`` that exact ``$def`` — never redefine it inline. This is the
#       generalized enum-drift guardrail: an enum term can only be spelled via the
#       shared ``$ref``, so its casing/values cannot diverge and no gate needs a
#       case-insensitive workaround to paper over a drifted example;
#   (c) an inline ``enum`` identical to a vocabulary enum ``$def`` (under any field
#       name) is a copy that will drift — it must ``$ref`` instead;
#   (d) every ``x-finding-adapter`` role/source key is a canonical term and every
#       severity-map target is a real ``severity`` enum value (a mistyped transform
#       would otherwise synthesise an invalid finding silently).
# ---------------------------------------------------------------------------

_VOCABULARY_REL = "_shared/vocabulary.schema.yaml"


def _ref_target_def(node: Any) -> str | None:
    """``{$ref: '..#/$defs/severity'}`` → ``'severity'`` (else ``None``)."""
    if not isinstance(node, dict):
        return None
    ref = node.get("$ref")
    if isinstance(ref, str) and "#/$defs/" in ref:
        return ref.split("#/$defs/", 1)[1]
    return None


def _iter_named_properties(node: Any, owner: str = ""):
    """Yield ``(owner, name, subschema)`` for every property at any depth.

    Descends through ``properties``/``items``/``allOf``/``anyOf``/``oneOf`` but NOT
    through ``$ref`` — a ``$ref`` is the desired terminal (its target is defined in
    the file it points at), so the vocabulary defs never re-enter as agent fields.
    """
    if not isinstance(node, dict):
        return
    props = node.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            if isinstance(sub, dict):
                yield owner, name, sub
                if _ref_target_def(sub) is None and "$ref" not in sub:
                    yield from _iter_named_properties(sub, f"{owner}{name}.")
    items = node.get("items")
    if isinstance(items, dict) and _ref_target_def(items) is None and "$ref" not in items:
        yield from _iter_named_properties(items, f"{owner}[].")
    for combiner in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(combiner) or []:
            yield from _iter_named_properties(sub, owner)


def validate_extractor_vocab_coherence(
    graph: Any, *, schema_dir: Path | None = None
) -> CoherenceReport:
    """Static extractor↔vocabulary coherence for every finding schema (see module
    section above). ``graph`` is accepted for signature symmetry with the other
    layers but the check is graph-independent — it scans the schema SSOT directly,
    exactly as the extractor does, so a new schema is covered with no edit here.
    Returns a :class:`CoherenceReport` (all findings are ERRORs)."""
    schema_dir = schema_dir or paths.schema_dir()
    report = CoherenceReport()

    # A config bundle may carry NO shared vocabulary — each agent's contract is then
    # self-contained (the extractor reads canonical keys / per-schema adapters directly),
    # so there is no shared SSOT to keep honest and this layer has nothing to prove.
    # Absence is a valid design; only a PRESENT-but-broken vocabulary is an error.
    if not (schema_dir / _VOCABULARY_REL).exists():
        return report

    try:
        vocab = load_schema_document(_VOCABULARY_REL, schema_dir)
    except Exception as exc:
        report.errors.append(f"vocabulary: cannot load {_VOCABULARY_REL!r}: {exc}")
        return report

    raw_defs = vocab.get("$defs")
    defs = raw_defs if isinstance(raw_defs, dict) else {}
    # (a) every $def documents itself.
    for name, spec in defs.items():
        desc = spec.get("description") if isinstance(spec, dict) else None
        if not (isinstance(desc, str) and desc.strip()):
            report.errors.append(
                f"vocabulary: $def {name!r} has no non-empty `description` — every canonical "
                f"term must document its meaning (build_schema_hint surfaces it to agents)"
            )

    enum_defs = {
        n: tuple(s["enum"])
        for n, s in defs.items()
        if isinstance(s, dict) and isinstance(s.get("enum"), list)
    }
    severity_values = set(enum_defs.get("severity", ()))

    for path in sorted(schema_dir.glob("*.schema.yaml")):
        rel = path.name
        try:
            schema = load_schema_document(rel, schema_dir)
        except Exception as exc:
            report.errors.append(f"{rel}: cannot load: {exc}")
            continue

        # (b)+(c): field-name and inline-enum drift.
        for owner, name, sub in _iter_named_properties(schema):
            if name in defs and _ref_target_def(sub) != name:
                got = sub.get("$ref") if "$ref" in sub else "inline definition"
                report.errors.append(
                    f"{rel}: field {owner}{name!r} shadows canonical vocabulary term {name!r} "
                    f"but does not `$ref` it (got {got!r}) — one term name = one type; a "
                    f"canonical name must `$ref` its single vocabulary $def"
                )
            inline_enum = sub.get("enum")
            if isinstance(inline_enum, list):
                for def_name, def_values in enum_defs.items():
                    if tuple(inline_enum) == def_values:
                        report.errors.append(
                            f"{rel}: field {owner}{name!r} inlines an enum identical to "
                            f"vocabulary $def {def_name!r} — replace it with a `$ref` so its "
                            f"values/casing cannot drift from the SSOT"
                        )

        # (d): synth-adapter role/source keys + severity-map targets stay in the vocab.
        for pname, pspec in (schema.get("properties") or {}).items():
            if not (isinstance(pspec, dict) and pspec.get("x-finding-array")):
                continue
            adapter = pspec.get("x-finding-adapter")
            if not isinstance(adapter, dict):
                continue
            for role in ("id", "title", "description", "category"):
                key = adapter.get(role)
                if isinstance(key, str) and key not in defs:
                    report.errors.append(
                        f"{rel}: x-finding-adapter on {pname!r} maps role {role!r} to source key "
                        f"{key!r}, which is not a canonical vocabulary term"
                    )
            sev = adapter.get("severity")
            sev_from = (
                sev.get("from")
                if isinstance(sev, dict)
                else (sev if isinstance(sev, str) else None)
            )
            if isinstance(sev_from, str) and sev_from not in defs:
                report.errors.append(
                    f"{rel}: x-finding-adapter on {pname!r} derives severity from source key "
                    f"{sev_from!r}, which is not a canonical vocabulary term"
                )
            if isinstance(sev, dict):
                for src, tgt in (sev.get("map") or {}).items():
                    if tgt not in severity_values:
                        report.errors.append(
                            f"{rel}: x-finding-adapter on {pname!r} maps severity {src!r}->{tgt!r} "
                            f"but {tgt!r} is not a `severity` enum value ({sorted(severity_values)})"
                        )

    return report


def validate_finding_adapter_targets(
    graph: Any, *, schema_dir: Path | None = None
) -> CoherenceReport:
    """Every ``x-finding-adapter.locations`` alias must resolve to a real array
    property (and real row sub-keys) on the finding item.

    Vocabulary-independent, so it runs for every bundle -- including ones with no
    shared vocabulary. The finding extractor reads the locations array by the
    adapter's ``from`` key and its rows by the sub-key names; a typo there silently
    yields zero locations (no gate fires, the finding just loses its evidence). This
    proves the alias names something the schema actually declares. ``graph`` is
    accepted for signature symmetry; the check scans the schema SSOT directly.
    Returns a :class:`CoherenceReport` (all findings are ERRORs)."""
    schema_dir = schema_dir or paths.schema_dir()
    report = CoherenceReport()
    for path in sorted(schema_dir.glob("*.schema.yaml")):
        rel = path.name
        try:
            schema = load_schema_document(rel, schema_dir)
        except Exception as exc:
            report.errors.append(f"{rel}: cannot load: {exc}")
            continue
        for pname, pspec in (schema.get("properties") or {}).items():
            if not (isinstance(pspec, dict) and pspec.get("x-finding-array")):
                continue
            adapter = pspec.get("x-finding-adapter")
            loc = adapter.get("locations") if isinstance(adapter, dict) else None
            if not isinstance(loc, dict):
                continue
            item = pspec.get("items")
            item_props = item.get("properties") if isinstance(item, dict) else None
            if not isinstance(item_props, dict):
                report.errors.append(
                    f"{rel}: x-finding-adapter on {pname!r} declares a `locations` alias but the "
                    f"finding item exposes no inline `properties` to resolve it against"
                )
                continue
            from_key = loc.get("from", "locations")
            loc_prop = item_props.get(from_key)
            if not isinstance(loc_prop, dict):
                report.errors.append(
                    f"{rel}: x-finding-adapter on {pname!r} aliases locations `from` {from_key!r}, "
                    f"which is not a property of the finding item -- the extractor would read no "
                    f"locations"
                )
                continue
            if loc_prop.get("type") != "array":
                report.errors.append(
                    f"{rel}: x-finding-adapter on {pname!r} aliases locations `from` {from_key!r}, "
                    f"but that property is not typed as an `array`"
                )
                continue
            row = loc_prop.get("items")
            row_props = row.get("properties") if isinstance(row, dict) else None
            if not isinstance(row_props, dict):
                continue
            for canonical in ("filePath", "startLine", "endLine"):
                actual = loc.get(canonical, canonical)
                if actual not in row_props:
                    report.errors.append(
                        f"{rel}: x-finding-adapter on {pname!r} maps location field {canonical!r} "
                        f"to row key {actual!r}, which is not a property of {from_key!r} rows"
                    )
    return report

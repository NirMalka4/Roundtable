"""Declarative edge predicates — the ``when:`` clause on a conditional edge.

A predicate is a small, offline-validatable expression over a producer's
schema-validated output. It gates a *consumer's activation*: when a required
edge carries a ``when:`` whose predicate is **false** against the edge's
``source`` output, the executor **skips** that consumer (reusing the existing
``skipped``/``na`` tri-state — no new control-flow state).

Grammar (declarative only — Q1: no registered-fn escape hatch):

  * leaf  — ``{field: <dotted.path>, equals: <scalar>}``
            or ``{field: <dotted.path>, in: [<scalar>, ...]}``
  * all   — ``{all: [<predicate>, ...]}``  (every operand true)
  * any   — ``{any: [<predicate>, ...]}``  (at least one operand true)

Every leaf reads the SAME producer — the output of the edge the ``when:`` sits
on — so a predicate never names a producer; the edge's ``source`` is implicit.
This module is pure (no graph imports): it owns the model, the parse (fail-loud
on malformed), and the evaluation. Offline field-path↔``output_schema`` coherence
is a separate doctor layer, not this module's concern.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_MISSING = object()

# Leaf comparison operators and the compound connectives. Exactly one appears in
# any single predicate mapping.
_LEAF_OPS = ("equals", "in")
_COMPOUND_OPS = ("all", "any")


@dataclass(frozen=True)
class Predicate:
    """A parsed, normalized ``when:`` expression.

    Exactly one shape per instance: a leaf (``op`` in :data:`_LEAF_OPS`, with
    ``field`` + ``value``) or a compound (``op`` in :data:`_COMPOUND_OPS`, with
    non-empty ``operands``). Frozen + hashable so a :class:`GraphEntry` stays
    frozen and round-trips by value equality.
    """

    op: str
    field: str | None = None
    value: Any = None  # scalar for 'equals'; tuple for 'in'
    operands: tuple[Predicate, ...] = ()

    def field_paths(self) -> tuple[str, ...]:
        """Every leaf field-path in the tree, in order (for doctor coherence)."""
        if self.op in _COMPOUND_OPS:
            return tuple(p for o in self.operands for p in o.field_paths())
        return (self.field,) if self.field else ()


def parse_predicate(raw: Any) -> Predicate:
    """Parse a ``when:`` mapping into a :class:`Predicate`. Fail loud on malformed.

    Raises ``ValueError`` with a precise reason (unknown/multiple operators, a
    non-string ``field``, an empty/non-list ``in`` or compound operand list) so a
    malformed predicate fails at graph-build, never silently at runtime.
    """
    if not isinstance(raw, Mapping):
        raise ValueError(f"when: must be a mapping, got {type(raw).__name__}")
    ops = [k for k in (*_COMPOUND_OPS, *_LEAF_OPS) if k in raw]
    if not ops:
        raise ValueError(
            f"when: no known operator (expected one of "
            f"{', '.join((*_COMPOUND_OPS, *_LEAF_OPS))}); got keys {sorted(raw)}"
        )
    if len(ops) > 1:
        raise ValueError(f"when: exactly one operator allowed, got {ops}")
    op = ops[0]

    if op in _COMPOUND_OPS:
        operands = raw[op]
        if not isinstance(operands, (list, tuple)) or not operands:
            raise ValueError(f"when.{op}: must be a non-empty list of predicates")
        return Predicate(op=op, operands=tuple(parse_predicate(o) for o in operands))

    field = raw.get("field")
    if not isinstance(field, str) or not field:
        raise ValueError(f"when.{op}: requires a non-empty string 'field'")
    if op == "in":
        values = raw["in"]
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError("when.in: must be a non-empty list of values")
        return Predicate(op=op, field=field, value=tuple(values))
    return Predicate(op=op, field=field, value=raw["equals"])


def predicate_to_dict(p: Predicate) -> dict[str, Any]:
    """Serialize back to the ``when:`` mapping. Inverse of :func:`parse_predicate`."""
    if p.op in _COMPOUND_OPS:
        return {p.op: [predicate_to_dict(o) for o in p.operands]}
    if p.op == "in":
        return {"field": p.field, "in": list(p.value)}
    return {"field": p.field, "equals": p.value}


def _resolve_path(output: Mapping[str, Any], path: str) -> Any:
    """Traverse a dotted ``a.b.c`` path; ``_MISSING`` if any hop is absent."""
    cur: Any = output
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def evaluate_predicate(p: Predicate, output: Mapping[str, Any]) -> bool:
    """Evaluate ``p`` against a producer's parsed output.

    A missing field-path is **false** (the condition is simply unmet — never an
    error). Compound nodes short-circuit via Python's ``all``/``any``.
    """
    if p.op == "all":
        return all(evaluate_predicate(o, output) for o in p.operands)
    if p.op == "any":
        return any(evaluate_predicate(o, output) for o in p.operands)
    actual = _resolve_path(output, p.field or "")
    if actual is _MISSING:
        return False
    if p.op == "equals":
        return bool(actual == p.value)
    if p.op == "in":
        return actual in p.value
    return False  # unreachable: parse rejects unknown ops

"""Unit tests for the declarative edge-predicate language (``when:``)."""

from __future__ import annotations

import pytest

from roundtable.graph.predicates import (
    Predicate,
    evaluate_predicate,
    parse_predicate,
    predicate_to_dict,
)


# ── parse ────────────────────────────────────────────────────────────────────
def test_parse_leaf_equals():
    p = parse_predicate({"field": "verdict", "equals": "revise"})
    assert p == Predicate(op="equals", field="verdict", value="revise")


def test_parse_leaf_in_normalizes_to_tuple():
    p = parse_predicate({"field": "risk", "in": ["high", "critical"]})
    assert p == Predicate(op="in", field="risk", value=("high", "critical"))


def test_parse_compound_all_any_nested():
    raw = {
        "all": [
            {"field": "risk", "equals": "high"},
            {"any": [{"field": "area", "equals": "sql"}, {"field": "area", "equals": "auth"}]},
        ]
    }
    p = parse_predicate(raw)
    assert p.op == "all"
    assert p.operands[0] == Predicate(op="equals", field="risk", value="high")
    assert p.operands[1].op == "any"
    assert len(p.operands[1].operands) == 2


@pytest.mark.parametrize(
    "raw, msg",
    [
        ({}, "no known operator"),
        ({"field": "x", "equals": 1, "in": [1]}, "exactly one operator"),
        ({"equals": "x"}, "requires a non-empty string 'field'"),
        ({"field": "", "equals": "x"}, "requires a non-empty string 'field'"),
        ({"field": "x", "in": []}, "non-empty list"),
        ({"all": []}, "non-empty list of predicates"),
        ("nope", "must be a mapping"),
    ],
)
def test_parse_rejects_malformed(raw, msg):
    with pytest.raises(ValueError, match=msg):
        parse_predicate(raw)


# ── round-trip ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw",
    [
        {"field": "verdict", "equals": "revise"},
        {"field": "risk", "in": ["high", "critical"]},
        {"all": [{"field": "a", "equals": 1}, {"any": [{"field": "b", "in": [2, 3]}]}]},
    ],
)
def test_predicate_to_dict_roundtrips(raw):
    assert predicate_to_dict(parse_predicate(raw)) == raw
    # idempotent: parse ∘ serialize ∘ parse is stable
    assert parse_predicate(predicate_to_dict(parse_predicate(raw))) == parse_predicate(raw)


# ── evaluate ─────────────────────────────────────────────────────────────────
def test_evaluate_equals_and_missing_is_false():
    p = parse_predicate({"field": "verdict", "equals": "revise"})
    assert evaluate_predicate(p, {"verdict": "revise"}) is True
    assert evaluate_predicate(p, {"verdict": "approve"}) is False
    assert evaluate_predicate(p, {}) is False  # missing field ⇒ false, never raises


def test_evaluate_in():
    p = parse_predicate({"field": "risk", "in": ["high", "critical"]})
    assert evaluate_predicate(p, {"risk": "high"}) is True
    assert evaluate_predicate(p, {"risk": "low"}) is False


def test_evaluate_dotted_path():
    p = parse_predicate({"field": "summary.risk", "equals": "high"})
    assert evaluate_predicate(p, {"summary": {"risk": "high"}}) is True
    assert evaluate_predicate(p, {"summary": {"risk": "low"}}) is False
    assert evaluate_predicate(p, {"summary": "notamap"}) is False  # bad shape ⇒ false


def test_evaluate_compound_all_any():
    p = parse_predicate(
        {"all": [{"field": "a", "equals": 1}, {"any": [{"field": "b", "equals": 2}]}]}
    )
    assert evaluate_predicate(p, {"a": 1, "b": 2}) is True
    assert evaluate_predicate(p, {"a": 1, "b": 9}) is False
    assert evaluate_predicate(p, {"a": 0, "b": 2}) is False


def test_field_paths_collects_every_leaf():
    p = parse_predicate(
        {"all": [{"field": "a.b", "equals": 1}, {"any": [{"field": "c", "in": [2]}]}]}
    )
    assert p.field_paths() == ("a.b", "c")

"""Unit tests for the JSON-value semantics helpers (_jscompat)."""

from __future__ import annotations

from roundtable.validation._jscompat import (
    MISSING,
    is_js_integer,
    is_js_number,
    js_str,
    js_typeof,
    nullish,
    v8_json_error_message,
)


def test_js_typeof_mirrors_javascript():
    assert js_typeof(None) == "object"  # typeof null === 'object'
    assert js_typeof([]) == "object"  # arrays are 'object'
    assert js_typeof({}) == "object"
    assert js_typeof(True) == "boolean"
    assert js_typeof(1) == "number"
    assert js_typeof(1.5) == "number"
    assert js_typeof("x") == "string"
    assert js_typeof(MISSING) == "undefined"


def test_is_js_number_excludes_bool():
    assert is_js_number(1) is True
    assert is_js_number(1.5) is True
    assert is_js_number(True) is False
    assert is_js_number("1") is False


def test_is_js_integer_matches_number_isinteger():
    assert is_js_integer(5) is True
    assert is_js_integer(5.0) is True  # Number.isInteger(5.0) === true
    assert is_js_integer(5.5) is False
    assert is_js_integer(True) is False
    assert is_js_integer("5") is False
    assert is_js_integer(float("nan")) is False
    assert is_js_integer(float("inf")) is False


def test_nullish():
    assert nullish(None, "x") == "x"
    assert nullish(MISSING, "x") == "x"
    assert nullish("", "x") == ""  # only null/undefined fall through
    assert nullish(0, "x") == 0


def test_js_str():
    assert js_str("foo") == "foo"
    assert js_str(None) == "null"
    assert js_str(MISSING) == "undefined"
    assert js_str(True) == "true"
    assert js_str(5) == "5"
    assert js_str(5.0) == "5"


def test_v8_json_error_start_of_input():
    # The only shape the oracle exercises (DeterministicPreScan prose output).
    assert (
        v8_json_error_message("Deterministic Pre-Scan: No obvious issues found.")
        == "Unexpected token 'D', \"Determinis\"... is not valid JSON"
    )
    # <=10 chars -> no ellipsis.
    assert v8_json_error_message("Det") == "Unexpected token 'D', \"Det\" is not valid JSON"
    assert (
        v8_json_error_message("Determinis")
        == "Unexpected token 'D', \"Determinis\" is not valid JSON"
    )
    # empty -> EOF message.
    assert v8_json_error_message("") == "Unexpected end of JSON input"

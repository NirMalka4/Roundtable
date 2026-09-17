"""JSON-value semantics that Python gets wrong by default.

JSON has one number type and a `null` distinct from an absent key; Python has
`bool` as an `int` subclass, `5.0 != 5` by type, and `None` for both. Agent
output is JSON, and the OVG gates + the finding extractor must judge it by
*JSON's* rules, not Python's — otherwise `true` validates as a number and
`5.0` fails an integer check. These helpers are the single home for that.

They are named after the JS operators whose semantics they mirror because the
validator was ported from a TypeScript implementation; `v8_json_error_message`
still reproduces V8's parse-failure string, which is surfaced to agents on retry
and persisted to the trace.
"""

from __future__ import annotations

import json
import math
from typing import Any, TypeGuard

# Sentinel for an absent object key — JS `undefined` (distinct from JSON null).
MISSING: Any = object()

# Characters that can legally start a JSON value. Used only to classify a
# parse failure as "unexpected token at start of input" (the V8 message shape
# we reproduce). See v8_json_error_message.
_JSON_VALUE_START = set('{["-0123456789tfn')


def js_typeof(value: Any) -> str:
    """Mirror JS `typeof` for JSON-derived values.

    Notably: `typeof null === 'object'`, arrays are `'object'`, and an absent
    key (our MISSING sentinel) is `'undefined'`.
    """
    if value is MISSING:
        return "undefined"
    if value is None:
        return "object"  # typeof null === 'object'
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"  # list / dict / anything else


def is_js_number(value: Any) -> TypeGuard[float]:
    """JS `typeof v === 'number'` — excludes Python bool (a JS boolean)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_js_integer(value: Any) -> bool:
    """Mirror `Number.isInteger(v)` — true only for integral JS numbers."""
    if not is_js_number(value):
        return False
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return False
    return float(value).is_integer()


def nullish(value: Any, default: Any) -> Any:
    """Mirror the JS `value ?? default` operator (null/undefined only)."""
    return default if (value is None or value is MISSING) else value


def _js_number_to_string(value: float | int) -> str:
    """Mirror `String(number)` for the integral / simple cases we hit."""
    if isinstance(value, bool):  # defensive; handled earlier normally
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value.is_integer():
        return str(int(value))
    return repr(value)


def js_str(value: Any) -> str:
    """Mirror JS `String(value)` for the value shapes the validator stringifies."""
    if value is MISSING:
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _js_number_to_string(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join(js_str(v) for v in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def v8_json_error_message(text: str) -> str:
    """Reproduce V8's `JSON.parse` failure message for `text`.

    Scope (agreed for the port): only the *start-of-input unexpected token*
    shape is reproduced byte-exactly — it is the only `Invalid JSON:` string
    the oracle exercises (e.g. DeterministicPreScan's prose output) and the
    realistic agent-failure case. Other V8 shapes (mid-string, EOF after a
    valid prefix) are out of corpus; a best-effort message is returned.

    `text` must be the same (trimmed) string passed to the failing parse.
    """
    if text == "":
        return "Unexpected end of JSON input"

    first = text[0]
    if first not in _JSON_VALUE_START:
        snippet = text[:10]
        ellipsis = "..." if len(text) > 10 else ""
        return f"Unexpected token '{first}', \"{snippet}\"{ellipsis} is not valid JSON"

    # Out-of-corpus fallback: not guaranteed to byte-match V8.
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return exc.msg
    return "is not valid JSON"

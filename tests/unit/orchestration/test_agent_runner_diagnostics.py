"""Unit tests for the problematic-part snippet extractor (agent_runner.diagnostics)."""

from __future__ import annotations

import json

from roundtable.engine.agent_runner.diagnostics import (
    FORMAT_WINDOW,
    SNIPPET_CAP,
    extract_snippet,
    structured_diags,
)
from roundtable.validation.pipeline import LeveledDiagnostic


def test_extract_snippet_format_failure_uses_bounded_raw_window() -> None:
    # parsed is None ⇒ unparseable output: a bounded head+tail window of the raw text.
    raw = "Here is my analysis:\n" + "x" * 5000
    snip = extract_snippet(None, "", raw)
    assert snip.startswith("Here is my analysis")
    assert "…[+" in snip  # middle elided
    assert len(snip) < len(raw)
    assert len(snip) <= FORMAT_WINDOW + 40  # cap + the elision marker


def test_extract_snippet_navigates_to_offending_value() -> None:
    parsed = {"findings": [{"id": "a", "severity": "HUGE"}]}
    assert extract_snippet(parsed, "findings[0].severity", json.dumps(parsed)) == '"HUGE"'


def test_extract_snippet_missing_path_returns_empty() -> None:
    parsed = {"findings": []}
    assert extract_snippet(parsed, "findings[3].severity", "{}") == ""


def test_extract_snippet_caps_large_values() -> None:
    parsed = {"blob": "y" * 5000}
    snip = extract_snippet(parsed, "blob", json.dumps(parsed))
    assert len(snip) <= SNIPPET_CAP + 40
    assert "…[+" in snip


def test_structured_diags_shape_and_snippet() -> None:
    parsed = {"findings": [{"id": "a", "severity": "HUGE"}]}
    diags = [
        LeveledDiagnostic(
            gate="json_schema",
            level="error",
            path="findings[0].severity",
            message="bad enum",
        ),
        LeveledDiagnostic(gate="format", level="error", path="", message="root msg"),
    ]
    out = structured_diags(diags, parsed, json.dumps(parsed))
    assert out[0] == {
        "gate": "json_schema",
        "path": "findings[0].severity",
        "message": "bad enum",
        "snippet": '"HUGE"',
    }
    # An empty path serialises the whole document as the snippet; no ``path`` key.
    assert "path" not in out[1]
    assert out[1]["gate"] == "format"

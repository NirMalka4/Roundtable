"""Excerpt grounding: faithful-by-construction derivation + path safety."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from roundtable.grounding.source_excerpt import (
    _ExcerptSpec,
    _specs_from_schema,
    ground_response_excerpts,
)

_SPEC = (
    _ExcerptSpec(
        finding_array="findings",
        anchor_array="anchors",
        file_key="filePath",
        start_key="startLine",
        end_key="endLine",
        excerpt_key="excerpt",
    ),
)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _resp(anchors: list[dict]) -> str:
    return json.dumps({"findings": [{"id": "NS-01", "anchors": anchors}]})


def _excerpts(response: str) -> list:
    data = json.loads(response)
    return [a.get("excerpt") for a in data["findings"][0]["anchors"]]


# --- spec parsing -----------------------------------------------------------


def test_marker_present_yields_spec() -> None:
    schema = {
        "properties": {
            "findings": {
                "x-finding-array": True,
                "x-finding-adapter": {"locations": {"from": "anchors", "excerpt": "excerpt"}},
            }
        }
    }
    specs = _specs_from_schema(schema)
    assert specs == _SPEC


def test_no_excerpt_marker_yields_no_spec() -> None:
    schema = {
        "properties": {
            "findings": {
                "x-finding-array": True,
                "x-finding-adapter": {"locations": {"from": "anchors"}},
            }
        }
    }
    assert _specs_from_schema(schema) == ()


def test_non_finding_array_ignored() -> None:
    schema = {"properties": {"summary": {"type": "string"}}}
    assert _specs_from_schema(schema) == ()


# --- faithful derivation ----------------------------------------------------


def test_file_anchor_excerpt_overwritten(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.ts", "line1\nline2\nline3\nline4\n")
    resp = _resp(
        [
            {
                "source": "code",
                "filePath": "src/a.ts",
                "startLine": 2,
                "endLine": 3,
                "excerpt": "line2 ... paraphrased",
            }
        ]
    )
    out = ground_response_excerpts(resp, _SPEC, tmp_path)
    assert _excerpts(out) == ["line2\nline3"]


def test_already_faithful_returns_unchanged_bytes(tmp_path: Path) -> None:
    _write(tmp_path, "a.ts", "alpha\nbeta\n")
    resp = _resp(
        [{"source": "code", "filePath": "a.ts", "startLine": 1, "endLine": 1, "excerpt": "alpha"}]
    )
    assert ground_response_excerpts(resp, _SPEC, tmp_path) == resp


def test_single_line_span(tmp_path: Path) -> None:
    _write(tmp_path, "a.ts", "one\ntwo\nthree\n")
    resp = _resp(
        [{"source": "code", "filePath": "a.ts", "startLine": 3, "endLine": 3, "excerpt": "wrong"}]
    )
    assert _excerpts(ground_response_excerpts(resp, _SPEC, tmp_path)) == ["three"]


# --- non-file / fail-open passthrough --------------------------------------


def test_non_file_anchor_untouched(tmp_path: Path) -> None:
    resp = _resp(
        [
            {
                "source": "pr",
                "filePath": None,
                "startLine": None,
                "endLine": None,
                "excerpt": "PR #123 title",
            }
        ]
    )
    assert ground_response_excerpts(resp, _SPEC, tmp_path) == resp


def test_missing_file_fails_open(tmp_path: Path) -> None:
    resp = _resp(
        [{"source": "code", "filePath": "gone.ts", "startLine": 1, "endLine": 1, "excerpt": "kept"}]
    )
    assert ground_response_excerpts(resp, _SPEC, tmp_path) == resp


def test_out_of_range_end_fails_open(tmp_path: Path) -> None:
    _write(tmp_path, "a.ts", "only\n")
    resp = _resp(
        [{"source": "code", "filePath": "a.ts", "startLine": 1, "endLine": 9, "excerpt": "kept"}]
    )
    assert ground_response_excerpts(resp, _SPEC, tmp_path) == resp


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (-1, 1)])
def test_invalid_span_fails_open(tmp_path: Path, start: int, end: int) -> None:
    _write(tmp_path, "a.ts", "x\ny\n")
    resp = _resp(
        [
            {
                "source": "code",
                "filePath": "a.ts",
                "startLine": start,
                "endLine": end,
                "excerpt": "kept",
            }
        ]
    )
    assert ground_response_excerpts(resp, _SPEC, tmp_path) == resp


# --- path safety ------------------------------------------------------------


def test_traversal_rejected(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    _write(tmp_path, "secret.txt", "TOPSECRET\n")
    resp = _resp(
        [
            {
                "source": "code",
                "filePath": "../secret.txt",
                "startLine": 1,
                "endLine": 1,
                "excerpt": "kept",
            }
        ]
    )
    assert ground_response_excerpts(resp, _SPEC, root) == resp


def test_absolute_path_rejected(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("TOPSECRET\n", encoding="utf-8")
    resp = _resp(
        [
            {
                "source": "code",
                "filePath": str(outside),
                "startLine": 1,
                "endLine": 1,
                "excerpt": "kept",
            }
        ]
    )
    assert ground_response_excerpts(resp, _SPEC, root) == resp


@pytest.mark.skipif(sys.platform == "win32", reason="symlink perms flaky on CI Windows")
def test_symlink_escape_rejected(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("TOPSECRET\n", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    resp = _resp(
        [
            {
                "source": "code",
                "filePath": "link.txt",
                "startLine": 1,
                "endLine": 1,
                "excerpt": "kept",
            }
        ]
    )
    assert ground_response_excerpts(resp, _SPEC, root) == resp


# --- robustness -------------------------------------------------------------


def test_non_json_response_unchanged(tmp_path: Path) -> None:
    assert ground_response_excerpts("not json at all", _SPEC, tmp_path) == "not json at all"


def test_no_specs_is_noop(tmp_path: Path) -> None:
    resp = _resp(
        [{"source": "code", "filePath": "a.ts", "startLine": 1, "endLine": 1, "excerpt": "x"}]
    )
    assert ground_response_excerpts(resp, (), tmp_path) == resp


def test_mixed_anchors(tmp_path: Path) -> None:
    _write(tmp_path, "a.ts", "real1\nreal2\n")
    resp = _resp(
        [
            {"source": "code", "filePath": "a.ts", "startLine": 1, "endLine": 2, "excerpt": "para"},
            {
                "source": "pr",
                "filePath": None,
                "startLine": None,
                "endLine": None,
                "excerpt": "PR note",
            },
        ]
    )
    assert _excerpts(ground_response_excerpts(resp, _SPEC, tmp_path)) == [
        "real1\nreal2",
        "PR note",
    ]

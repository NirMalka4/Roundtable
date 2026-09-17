"""Remediation union parsing + anchorIndex→target resolution."""

from __future__ import annotations

from roundtable.extraction.finding_extractor import (
    NormalizedLocation,
    Remediation,
    RemediationCheck,
    _parse_remediation,
)

_LOCS = (
    NormalizedLocation(file_path="a.ts", start_line=1, end_line=2),
    NormalizedLocation(file_path="b.ts", start_line=5, end_line=5),
)
_RATIONALE = "This change blocks the proven failure and preserves intended behavior."
_CHECKS = [
    {
        "filePath": "src/caller.ts",
        "startLine": 8,
        "endLine": 9,
        "observation": "The caller consumes the same return value after the proposed change.",
    }
]


def test_suggestion_resolves_target() -> None:
    rem = _parse_remediation(
        {
            "kind": "suggestion",
            "rationale": _RATIONALE,
            "anchorIndex": 1,
            "replacement": "x = 1",
        },
        _LOCS,
    )
    assert rem == Remediation(
        kind="suggestion",
        rationale=_RATIONALE,
        replacement="x = 1",
        target=_LOCS[1],
    )


def test_suggestion_preserves_replacement_verbatim() -> None:
    rem = _parse_remediation(
        {
            "kind": "suggestion",
            "rationale": _RATIONALE,
            "anchorIndex": 0,
            "replacement": "    indented\n",
        },
        _LOCS,
    )
    assert rem is not None and rem.replacement == "    indented\n"


def test_checks_and_limitations_parse_directly() -> None:
    rem = _parse_remediation(
        {
            "kind": "fix",
            "rationale": _RATIONALE,
            "prose": "Change the caller.",
            "checks": _CHECKS,
            "limitations": ["The proposal was not executed."],
        },
        _LOCS,
    )
    assert rem is not None
    assert rem.checks == (
        RemediationCheck(
            location=NormalizedLocation("src/caller.ts", 8, 9),
            observation=("The caller consumes the same return value after the proposed change."),
        ),
    )
    assert rem.limitations == ("The proposal was not executed.",)


def test_malformed_check_rejects_the_remediation() -> None:
    assert (
        _parse_remediation(
            {
                "kind": "fix",
                "rationale": _RATIONALE,
                "prose": "Change the caller.",
                "checks": [
                    {
                        "filePath": "src/caller.ts",
                        "startLine": 9,
                        "endLine": 8,
                        "observation": "Reversed range.",
                    }
                ],
                "limitations": [],
            },
            _LOCS,
        )
        is None
    )


def test_suggestion_out_of_range_index_yields_no_target() -> None:
    rem = _parse_remediation(
        {
            "kind": "suggestion",
            "rationale": _RATIONALE,
            "anchorIndex": 9,
            "replacement": "x",
        },
        _LOCS,
    )
    assert rem is not None and rem.target is None


def test_suggestion_missing_replacement_is_none() -> None:
    assert (
        _parse_remediation(
            {"kind": "suggestion", "rationale": _RATIONALE, "anchorIndex": 0},
            _LOCS,
        )
        is None
    )


def test_rationale_remains_optional_for_other_bundles() -> None:
    assert _parse_remediation(
        {"kind": "suggestion", "anchorIndex": 0, "replacement": "x"},
        _LOCS,
    ) == Remediation(
        kind="suggestion",
        replacement="x",
        target=_LOCS[0],
    )


def test_fix_variant() -> None:
    assert _parse_remediation(
        {"kind": "fix", "rationale": f"  {_RATIONALE}  ", "prose": " do X "},
        _LOCS,
    ) == Remediation(kind="fix", rationale=_RATIONALE, prose="do X")


def test_draft_variant() -> None:
    rem = _parse_remediation(
        {"kind": "draft", "rationale": _RATIONALE, "code": "it('x')", "language": "ts"},
        _LOCS,
    )
    assert rem == Remediation(kind="draft", rationale=_RATIONALE, code="it('x')", language="ts")


def test_draft_language_reduced_to_first_token() -> None:
    """A multi-word/multi-line tag would break the fence it is interpolated into."""
    rem = _parse_remediation(
        {
            "kind": "draft",
            "rationale": _RATIONALE,
            "code": "x",
            "language": "ts jest\nmore",
        },
        _LOCS,
    )
    assert rem == Remediation(kind="draft", rationale=_RATIONALE, code="x", language="ts")


def test_draft_blank_language_is_none() -> None:
    rem = _parse_remediation(
        {"kind": "draft", "rationale": _RATIONALE, "code": "x", "language": "   "},
        _LOCS,
    )
    assert rem == Remediation(kind="draft", rationale=_RATIONALE, code="x", language=None)


def test_unknown_kind_is_none() -> None:
    assert (
        _parse_remediation(
            {"kind": "patch", "rationale": _RATIONALE, "prose": "x"},
            _LOCS,
        )
        is None
    )


def test_non_dict_is_none() -> None:
    assert _parse_remediation("oops", _LOCS) is None

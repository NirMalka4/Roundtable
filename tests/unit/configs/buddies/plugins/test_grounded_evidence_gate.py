"""Judge citations must land on the change a reader can open on the pull request.

The gate itself is the engine's field-name-agnostic `grounded_locations`; what is
buddies-specific — and what this locks — is that the bundle points it at the claim's
own evidence, so the `requires` path is read from `gates.yaml` rather than retyped.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import roundtable.configs.buddies as buddies_pkg
from roundtable.validation import gates
from roundtable.validation.gate_kit import GateRequest

_GATES = yaml.safe_load(
    (Path(buddies_pkg.__file__).parent / "gates.yaml").read_text(encoding="utf-8")
)["gates"]["grounded_evidence"]
_REQUIRES = tuple(_GATES["requires"])


def _paths(output: dict, changed: list[str]) -> list[str]:
    request = GateRequest("Judge", output, {"changed_files": changed}, requires=_REQUIRES)
    return [d.path for d in gates.grounded_locations_gate(request)]


def _claim(*files: str) -> dict:
    return {
        "claims": [{"evidence": [{"file": f, "observation": "x", "role": "defect"} for f in files]}]
    }


def test_bundle_points_the_gate_at_claim_evidence() -> None:
    assert _GATES["fn"] == "grounded_locations"
    assert _REQUIRES == ("claims[].evidence[].file",)


def test_citation_in_the_change_is_grounded() -> None:
    assert _paths(_claim("src/a.cs"), ["src/a.cs"]) == []


def test_citation_outside_the_change_is_flagged() -> None:
    assert _paths(_claim("src/a.cs", "ghost/b.cs"), ["src/a.cs"]) == ["claims[0].evidence[1].file"]


def test_advisory_so_an_offdiff_verification_can_still_publish() -> None:
    """A Judge check of an unchanged caller is legitimate, so this must not block."""
    assert _GATES["default_level"] == "warn"


def test_no_changed_files_makes_grounding_unverifiable_not_wrong() -> None:
    assert _paths(_claim("ghost/b.cs"), []) == []

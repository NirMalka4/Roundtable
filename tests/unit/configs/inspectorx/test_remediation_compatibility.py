"""Shared extraction changes do not alter InspectorX's legacy fix surface."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from roundtable.configs.inspectorx.plugins.configuration import inspectorx_configuration
from roundtable.extraction import extract_findings


def test_security_example_keeps_its_legacy_prose_fix() -> None:
    schema = yaml.safe_load(
        Path("roundtable/configs/inspectorx/schemas/security.schema.yaml").read_text(
            encoding="utf-8"
        )
    )
    payload = copy.deepcopy(schema["examples"][0])
    expected = payload["findings"][0]["fix"]
    finding = extract_findings(
        "Security",
        json.dumps(payload),
        inspectorx_configuration(),
    )[0]
    assert finding.fix == expected
    assert finding.remediation is None

from __future__ import annotations

import json
from pathlib import Path

from roundtable.application import run_configuration
from roundtable.graph import Configuration


def test_synthetic_workflow_uses_generic_engine_and_artifacts(tmp_path: Path) -> None:
    root = Path(__file__).parents[2] / "fixtures" / "generic_workflow"
    configuration = Configuration.from_file(root)

    run = run_configuration(
        configuration,
        {"Source": "caller payload\n"},
        backend="mock",
        output_dir=tmp_path,
        session_id="generic",
        max_attempts=1,
    )

    assert run.persist.exit_code == 0
    assert run.result.execution_order == ["Source", "Draft", "Structured", "Summary"]
    assert run.result.results["Draft"].response == '{"result":"sim:draft"}'
    assert json.loads(run.result.results["Structured"].response) == {"accepted": True}
    assert json.loads(run.result.results["Summary"].response) == {
        "accepted": True,
        "raw": '{"result":"sim:draft"}',
    }
    assert json.loads((run.persist.session_dir / "source-payloads.json").read_text()) == {
        "Source": "caller payload\n"
    }
    trace = json.loads(run.persist.trace_path.read_text())
    assert trace["workflow"] == {
        "configuration": "generic-workflow",
        "complete": True,
        "steps": 4,
    }
    assert "pull request" not in run.persist.report_path.read_text().lower()

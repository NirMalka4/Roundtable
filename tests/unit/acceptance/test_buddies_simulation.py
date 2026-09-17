from __future__ import annotations

import json
from pathlib import Path

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration
from roundtable.review.flow import run_review


def test_buddies_simulation_produces_an_accepted_judge_verdict(tmp_path: Path) -> None:
    configuration = get_configuration(resolve_bundle("buddies"))
    changed_file = "src/example.py"

    result = run_review(
        label="dev/buddies-simulation",
        config=configuration,
        backend_name="mock",
        base_dir=tmp_path,
        changed_files=[changed_file],
        source_payloads={
            "ReviewDiff": (
                f"diff --git a/{changed_file} b/{changed_file}\n"
                f"--- a/{changed_file}\n"
                f"+++ b/{changed_file}\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )
        },
    )

    judge = result.scheduler.results["Judge"]
    payload = json.loads(judge.response)

    assert judge.valid
    assert judge.attempts == 1
    assert judge.submission_status == "accepted"
    assert not judge.errors
    assert payload["claims"]
    assert result.verdict.verdict != "UNKNOWN"
    assert all(outcome.usage.input_tokens == 0 for outcome in result.scheduler.results.values())
    assert all(outcome.usage.output_tokens == 0 for outcome in result.scheduler.results.values())

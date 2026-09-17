from pathlib import Path

from roundtable.bundle import CONFIGS_DIR
from roundtable.graph import Configuration
from roundtable.runtime.agent_setup import full_system_prompt


def test_redgreen_prompt_requires_targeted_bounded_test_execution() -> None:
    root = Path(CONFIGS_DIR) / "buddies"
    entry = Configuration.from_file(root).by_key["redgreen"]
    prompt = full_system_prompt(entry, root / "prompts" / "Reviewer")
    prompt = " ".join(prompt.split())
    assert "smallest targeted selector" in prompt
    assert "monorepo-wide suites are prohibited" in prompt
    assert "selector scope and expected maximum duration" in prompt
    assert "`powershell` invocations are capped at 120 seconds" in prompt
    assert "detached execution is disabled" in prompt
    assert "`read_powershell.delay` is capped at 30 seconds" in prompt

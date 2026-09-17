"""The gate set (`scripts/gates.py`) and its GitHub Actions consumer.

The point of the script is that the gate list exists once. These tests guard that
property structurally - a workflow that re-inlines `ruff`/`pytest`/`pyright` fails
here — plus the two runner behaviors CI depends on: stop at the first red gate,
and name it (CI runs the gates as a single step, so that name is the only signal).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO / ".github" / "workflows" / "ci.yml"


def _load_gates():
    spec = importlib.util.spec_from_file_location("_script_gates", _REPO / "scripts" / "gates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gates = _load_gates()


def _workflow_commands() -> list[str]:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return [step["run"] for step in workflow["jobs"]["gates"]["steps"] if "run" in step]


# ── the pipeline delegates instead of duplicating ───────────────────────────
def test_ci_runs_the_gates_script_exactly_once_on_supported_python_versions():
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["jobs"]["gates"]["strategy"]["matrix"]["python-version"] == ["3.12", "3.13"]
    assert sum("scripts/gates.py" in command for command in _workflow_commands()) == 1


@pytest.mark.parametrize("tool", ["ruff", "pyright", "pytest", "check_release_version"])
def test_ci_does_not_re_inline_a_gate(tool):
    delegating = [command for command in _workflow_commands() if "scripts/gates.py" not in command]
    assert [command for command in delegating if tool in command] == []


# ── runner behavior ─────────────────────────────────────────────────────────
class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _stub_runs(monkeypatch, fail_on: str | None = None):
    calls: list[list[str]] = []

    def fake_run(command, cwd=None):
        calls.append(command)
        return _Completed(1 if fail_on is not None and fail_on in command else 0)

    monkeypatch.setattr(gates.subprocess, "run", fake_run)
    return calls


def test_all_green_runs_every_gate(monkeypatch):
    calls = _stub_runs(monkeypatch)
    assert gates.main([]) == 0
    assert len(calls) == len(gates.GATES)


def test_stops_at_the_first_red_gate(monkeypatch):
    calls = _stub_runs(monkeypatch, fail_on="pyright")
    assert gates.main([]) == 1
    assert calls[-1] == next(command for _, command in gates.GATES if "pyright" in command)


def test_unknown_flag_is_rejected_before_any_gate_runs(monkeypatch):
    calls = _stub_runs(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        gates.main(["--fast"])
    assert exit_info.value.code != 0
    assert calls == []


def test_failure_names_the_gate_on_stderr(monkeypatch, capsys):
    _stub_runs(monkeypatch, fail_on="pyright")
    assert gates.main([]) == 1
    assert "gate failed: pyright" in capsys.readouterr().err

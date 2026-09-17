"""Workflow-neutral application service over the graph engine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from roundtable.engine import Engine, RunOptions, RunResult
from roundtable.graph import Configuration
from roundtable.persistence import PersistResult, persist_session


@dataclass(frozen=True)
class ApplicationRun:
    result: RunResult
    persist: PersistResult


def run_configuration(
    configuration: Configuration,
    inputs: dict[str, str],
    *,
    backend: str,
    output_dir: str | Path,
    session_id: str | None = None,
    concurrency: int = 4,
    max_attempts: int = 3,
    cwd: str | None = None,
) -> ApplicationRun:
    """Run any configuration with caller-owned source payloads and persist its facts."""
    missing = sorted(_source_keys(configuration) - inputs.keys())
    unknown = sorted(inputs.keys() - _source_keys(configuration))
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing source inputs: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown source inputs: {', '.join(unknown)}")
        raise ValueError("; ".join(details))
    sid = session_id or datetime.now(UTC).strftime("run-%Y%m%dT%H%M%S%fZ")
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    result = Engine(
        backend,
        options=RunOptions(
            concurrency=concurrency,
            max_attempts=max_attempts,
            cwd=cwd,
        ),
    ).run(configuration, inputs)
    complete = result.terminated_within_budget and all(
        outcome.valid for outcome in result.results.values()
    )
    persist = persist_session(
        Path(output_dir),
        session_id=sid,
        agent_outcomes=result.results,
        overlay={
            "workflow": {
                "configuration": configuration.name,
                "complete": complete,
                "steps": result.steps,
            }
        },
        started_at=started,
        report_md=_report(configuration, result, complete),
        report_filename="report.md",
        exit_code=0 if complete else 1,
        outcome_label="complete" if complete else "failed",
        configuration=configuration,
        source_payloads=inputs,
        log_lines=[f"[run] configuration={configuration.name}", f"[run] steps={result.steps}"],
    )
    return ApplicationRun(result=result, persist=persist)


def load_inputs(bindings: list[str]) -> dict[str, str]:
    """Load repeated `KEY=PATH` bindings without interpreting their content."""
    inputs: dict[str, str] = {}
    for binding in bindings:
        key, separator, raw_path = binding.partition("=")
        if not separator or not key or not raw_path:
            raise ValueError(f"invalid input {binding!r}; expected KEY=PATH")
        if key in inputs:
            raise ValueError(f"duplicate input key {key!r}")
        path = Path(raw_path).expanduser()
        try:
            inputs[key] = path.read_text(encoding="utf-8")
        except OSError as err:
            raise ValueError(f"cannot read input {key!r} from {path}: {err}") from err
    return inputs


def _source_keys(configuration: Configuration) -> set[str]:
    return {entry.key for entry in configuration.entries if entry.kind == "source"}


def _report(configuration: Configuration, result: RunResult, complete: bool) -> str:
    lines = [
        f"# {configuration.name} run",
        "",
        f"Status: {'complete' if complete else 'failed'}",
        "",
        "## Nodes",
    ]
    for key in result.execution_order:
        outcome = result.results[key]
        lines.append(f"- `{key}`: {'valid' if outcome.valid else outcome.gate or 'invalid'}")
    lines.extend(("", "## Outputs", ""))
    for key in result.execution_order:
        lines.extend(
            (f"### {key}", "", "```json", _display(result.results[key].response), "```", "")
        )
    return "\n".join(lines)


def _display(response: str) -> str:
    try:
        return json.dumps(json.loads(response), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return response

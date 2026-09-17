"""mock_runner: a deterministic, OVG-valid stand-in for the live ``copilot`` LLM.

Purpose (the ``--simulate`` capability). Running the real review against a live PR
spends tokens. To verify the *context-assembly* path — session header, git
section, inter-agent **injection** (``## Context from <Dep>``), and the flat
dossier — without that cost, we run the **real scheduler** but swap the LLM
``run_fn`` for :func:`make_mock_run_agent`. Each agent then receives a synthetic
output that is *guaranteed to pass OVG validation*, so:

  * every producer's ``AgentRunOutcome.valid`` is ``True`` → downstream consumers
    get real ``## Context from <Dep> [REQUIRED]`` sections (verbatim injection),
    not ``[REQUIRED — UNAVAILABLE]`` stubs; and
  * the per-consumer ``Dossier_*`` deterministic nodes are populated from their
    producer deps, so each consumer (Judge / SeverityInflator / ExploitEngineer)
    receives its rendered concern dossier.

The stub is the agent's own declarative contract: its ``output_schema``'s canonical
``examples[0]``. That example satisfies every **context-free** gate by construction —
the doctor's Layer-5 coherence check proves it self-validates against the schema *and*
passes the context-free error gates (``locations_floor`` / ``proof_depth`` / …). The one
part a repo-agnostic example cannot embed is the **context gate** ``grounded_locations``
(it needs the review's diff), so the stub grounds each finding location's ``filePath``
against the actual ``changed_files`` — mirroring what a real agent does and what the
``grounded_locations`` gate checks. The stub therefore stays OVG-valid by construction as
schemas evolve.

This is a debug/inspection tool only — it never runs on the live path.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from roundtable.graph import Configuration, get_configuration, register_config_plugins
from roundtable.simulation import apply_simulation_transform
from roundtable.validation import load_gate_registry, load_schema_document

from .registry import BackendOptions, backend_context, normalize_backend_result
from .result import CopilotResult, OutputSubmission, RunRequest, RunResult
from .usage import EMPTY_USAGE


def _slug(agent: str) -> str:
    return re.sub(r"\W+", "_", agent).strip("_").lower()


def _replicate_finding(base: dict[str, Any], idx: int) -> dict[str, Any]:
    """Copy a canonical finding, giving it a unique ``id`` so a multi-finding stub
    populates distinct dossier records."""
    finding = deepcopy(base)
    if idx > 0 and "id" in finding:
        finding["id"] = f"{finding['id']}-{idx + 1}"
    return finding


def _ground_locations(example: dict[str, Any], changed_files: list[str]) -> None:
    """Repoint every finding location's ``filePath`` at a real changed file, in place.

    A schema example carries an illustrative path (e.g. ``src/repo/UserRepo.cs``) that
    is not in the review's diff, so the ``grounded_locations`` gate rejects it. Under
    ``--simulate`` the gate's ``changed_files`` context IS present, so agents that
    escalate it to ``level: error`` would fail on the canned path. Rewriting each
    ``filePath`` to the first changed file keeps the stub genuinely OVG-passing against
    the actual diff. No-op when the review has no changed files (the gate is a no-op too).
    """
    if not changed_files:
        return
    grounded = changed_files[0]
    findings = example.get("findings")
    if not isinstance(findings, list):
        return
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        for loc in finding.get("locations") or []:
            if isinstance(loc, dict) and "filePath" in loc:
                loc["filePath"] = grounded


def build_valid_stub(
    agent: str,
    *,
    configuration: Configuration | None = None,
    findings_per_agent: int = 1,
    changed_files: list[str] | None = None,
    validation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthesize an OVG-passing JSON object for ``agent`` from its schema example.

    Resolves the agent's ``output_schema`` from the graph (keyed by ``GraphEntry.key``,
    the value the scheduler passes) and returns a deep copy of its canonical
    ``examples[0]``. An agent with no declared schema falls back to a minimal
    non-empty object (never ``{}``, which the run loop rejects as empty output).

    ``findings_per_agent`` (default 1) resizes the example's ``findings`` array so the
    per-consumer ``Dossier_*`` nodes populate their concern dossiers under
    ``--simulate``. ``0`` keeps ``findings`` empty
    (structure-only); a value ``> 1`` replicates the canonical finding with unique ids.
    Agents whose example carries no findings are unaffected.

    ``changed_files`` (the review's diff) repoints each finding location's ``filePath``
    at a real changed file so the ``grounded_locations`` gate passes for agents that
    escalate it to ``level: error``; without it the canned example path is ungrounded.
    """
    config = configuration or get_configuration()
    register_config_plugins(config)
    entry = config.by_key.get(agent)
    if entry is None or entry.output_schema is None:
        return {"result": f"sim:{_slug(agent)}"}

    example = deepcopy(
        (
            load_schema_document(entry.output_schema, config.root / "schemas").get("examples")
            or [{}]
        )[0]
    )
    if not isinstance(example, dict):
        return {"result": f"sim:{_slug(agent)}"}

    findings = example.get("findings")
    if isinstance(findings, list) and findings and findings_per_agent != len(findings):
        if findings_per_agent <= 0:
            example["findings"] = []
        else:
            base = findings[0]
            example["findings"] = [_replicate_finding(base, i) for i in range(findings_per_agent)]
    context = dict(validation_context or {})
    if changed_files is not None:
        context.setdefault("changed_files", changed_files)
    _ground_locations(example, list(context.get("changed_files") or []))
    gate_registry = load_gate_registry(config.root / "gates.yaml")
    for binding in entry.ovg_gates or ():
        gate_name = binding.get("gate")
        if not isinstance(gate_name, str):
            continue
        spec = gate_registry.get(gate_name)
        if spec is not None:
            example = apply_simulation_transform(
                gate_name,
                example,
                context,
                spec.requires,
            )
    return example


def make_mock_run_agent(
    *,
    tag: str = "sim",
    changed_files: list[str] | None = None,
    configuration: Configuration | None = None,
):
    """Build a ``run_fn`` (drop-in for the SDK ``run_fn``) that returns a
    deterministic, OVG-valid :class:`CopilotResult` instead of calling the model.

    The returned callable accepts the same keyword surface as the SDK ``run_fn``.
    It uses ``agent`` to pick the stub and, when present, completes the same explicit
    output-submission contract as the live backend. ``final_content`` is the
    synthesized JSON for that agent.

    ``changed_files`` is the review's diff; it is forwarded to :func:`build_valid_stub`
    so finding locations ground against the actual changed files (see that function).
    """

    def run_fn(
        *,
        agent: str,
        prompt: str = "",
        model: str | None = None,
        add_dirs: list[str] | None = None,
        timeout_s: float = 600.0,
        mcp_servers: dict[str, Any] | None = None,
        submission: OutputSubmission | None = None,
        validation_context: dict[str, Any] | None = None,
        **_kw: Any,
    ) -> CopilotResult:
        output = build_valid_stub(
            agent,
            changed_files=changed_files,
            configuration=configuration,
            validation_context=validation_context,
        )
        if submission is not None:
            submission.submit(output)
        content = json.dumps(
            output,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        result = CopilotResult(
            final_content=content,
            tool_call_count=0,
            rounds=1,
            exit_code=0,
            session_id=f"{tag}-{_slug(agent)}",
            usage={},
            tools_used=[],
            events=[],
            timed_out=False,
            wall_clock_s=0.0,
            raw_stdout=content,
            raw_stderr="",
            mcp_servers=[],
            token_usage=EMPTY_USAGE,
        )
        return normalize_backend_result(
            RunRequest(agent=agent, prompt=prompt, submission=submission),
            result,
        )

    return run_fn


class MockBackend:
    def __init__(
        self,
        configuration: Configuration,
        *,
        tag: str = "sim",
        changed_files: tuple[str, ...] = (),
    ) -> None:
        self._configuration = configuration
        self._tag = tag
        self._changed_files = list(changed_files)

    def run(self, request: RunRequest) -> RunResult:
        output = build_valid_stub(
            request.agent,
            configuration=self._configuration,
            changed_files=self._changed_files,
            validation_context=dict(request.validation_context),
        )
        if request.submission is not None:
            request.submission.submit(output)
        content = json.dumps(
            output,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        result = RunResult(
            final_content=content,
            tool_call_count=0,
            rounds=1,
            exit_code=0,
            session_id=f"{self._tag}-{_slug(request.agent)}",
            raw_stdout="",
            token_usage=EMPTY_USAGE,
        )
        return normalize_backend_result(request, result)


def mock_backend(options: BackendOptions):
    return backend_context(
        MockBackend(
            options.configuration,
            changed_files=options.changed_files,
        )
    )

"""gates: the Buddies review bundle's OVG gate callables.

The generic ``validation`` package holds only the gate *mechanism* — the
``Diagnostic``/``GateRequest`` types, the registry, the schema loader, and the
task-agnostic primitives (``json_schema``, ``grounded_locations``). The gates that
encode THIS config's review contract live here, in the bundle, and register
themselves when this module is imported.

Loading is declarative, exactly like :mod:`.context_plugins`: ``agent_graph.yaml``
names this module in its top-level ``plugins:`` list, and
:func:`roundtable.graph.model.register_config_plugins` imports it (once)
before doctor-validate or a run resolves a gate ``fn`` by name. The generic
``validation`` package never imports this module — the dependency arrow points from
the bundle *into* the generic core, never out, and ``roundtable doctor`` proves it
(see ``roundtable/validation_boundary.py``).

What is Buddies-specific about these gates:

* ``claims_coverage`` — the adjudicator must account for every upstream reviewer
  finding. That universe is a *run* fact, not a shape, so no schema can express it.
* ``primary_source_selection`` — the Judge's primary claim source must belong to the
  same adjudicated claim.
* ``remediation_draft_shape`` — Buddies' single non-applyable remediation shape and
  its code-reading checks.
* ``remediation_decisions`` — exact coverage and evidence for the post-Judge
  publication decisions against the supplied dossier and successful read tools.
* ``execution_backed_evidence`` — binds a ``measured`` evidence claim to recorded
  tool telemetry, so the tag is engine-verified rather than model-asserted.
* ``claim_consistency`` — a claim's disposition and severity must name one legal
  adjudication cell, the pair the release effect is derived from.

Each ``register_gate_function`` name must match an ``fn:`` in
``configs/buddies/gates.yaml``; an unregistered ``fn`` fails ``load_gate_registry``
loudly rather than silently disabling a gate.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from roundtable.plugins import (
    SimulationRequest,
    register_gate_function,
    register_simulation_transform,
)
from roundtable.validation import (
    MISSING,
    Diagnostic,
    GateRequest,
    js_str,
    nonempty_str,
    nullish,
)

from .verdict import RULING_MATRIX


def _coverage_fields(request: GateRequest) -> tuple[str, str]:
    """Resolve ``(claims_field, cited_ids_field)`` for :func:`claims_coverage_gate`.

    Example: ``claims[].source_finding_ids`` → ``("claims", "source_finding_ids")``.
    """
    claims, cited = request.field_names(
        slots=("claims", "cited"),
        defaults=("claims", "source_finding_ids"),
    )
    return claims, cited


def claims_coverage_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Context gate: every upstream finding must be adjudicated by at least one claim.

    Replaces a soft, prompt-only "account for every finding" checksum with an enforced
    completeness check. The universe of ``agent::finding_id``s the adjudicator must cover
    is supplied out-of-band as ``context['upstream_finding_ids']`` (the node handler
    derives it from the run snapshot); each claim declares the finding(s) it adjudicates
    in a cited-ids field resolved from the gate's ``requires`` (default
    ``claims[].source_finding_ids``). Neutral: one diagnostic per uncovered finding; the
    runner stamps the level. When no universe is supplied (unverifiable), the gate is a
    no-op — mirroring :func:`grounded_locations_gate`.
    """
    claims_field, cited_field = _coverage_fields(request)
    if not isinstance(request.output.get(claims_field), list):
        return []  # container shape is the json_schema gate's call, not this one's
    diags: list[Diagnostic] = []
    seen_ids: set[str] = set()
    for i, claim in request.findings(claims_field):
        claim_id = claim.get("id")
        if isinstance(claim_id, str):
            if claim_id in seen_ids:
                diags.append(
                    Diagnostic(
                        path=f"{claims_field}[{i}].id",
                        message=f"duplicate claim id {claim_id!r}",
                    )
                )
            seen_ids.add(claim_id)
    if "upstream_finding_ids" not in request.context:
        return diags
    universe = {
        i for i in (request.context.get("upstream_finding_ids") or []) if isinstance(i, str)
    }
    covered: set[str] = set()
    for _, claim in request.findings(claims_field):
        cited = claim.get(cited_field)
        for c in cited if isinstance(cited, list) else ():
            if isinstance(c, str):
                covered.add(c)
    diags.extend(
        Diagnostic(
            path=claims_field,
            message=f"claim cites unknown reviewer finding {fid!r}",
        )
        for fid in sorted(covered - universe)
    )
    diags.extend(
        Diagnostic(
            path=claims_field,
            message=f"reviewer finding {fid!r} is not adjudicated by any claim (uncovered)",
        )
        for fid in sorted(universe - covered)
    )
    return diags


def primary_source_selection_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Require the primary claim source to belong to the same Judge claim."""
    diags: list[Diagnostic] = []
    for i, claim in request.findings("claims"):
        selected = claim.get("primary_source_finding_id")
        cited = claim.get("source_finding_ids")
        if isinstance(selected, str) and (not isinstance(cited, list) or selected not in cited):
            diags.append(
                Diagnostic(
                    f"claims[{i}].primary_source_finding_id",
                    "must identify a reviewer finding adjudicated by this claim",
                )
            )
    return diags


def _simulation_coverage_fields(requires: tuple[str, ...]) -> tuple[str, str]:
    for path in requires:
        match = re.fullmatch(r"([A-Za-z_]\w*)\[\]\.([A-Za-z_]\w*)", path)
        if match:
            return match.group(1), match.group(2)
    raise ValueError("claims_coverage simulation requires an array citation path")


def claims_coverage_simulation(request: SimulationRequest, /) -> dict[str, Any]:
    """Cover the review's actual finding ids with deterministic synthetic claims."""
    claims_field, cited_field = _simulation_coverage_fields(request.requires)
    identifiers = [
        item for item in request.context.get("upstream_finding_ids", ()) if isinstance(item, str)
    ]
    cited_file = next(
        (f for f in request.context.get("changed_files", ()) if isinstance(f, str) and f),
        "simulated/changed_file",
    )
    claims = []
    for index, identifier in enumerate(dict.fromkeys(identifiers), start=1):
        claims.append(
            {
                "id": f"J-{index:02d}",
                cited_field: [identifier],
                "primary_source_finding_id": identifier,
                "title": "Synthetic reviewer claim reaches adjudication",
                "criterion": "other",
                "disposition": "upheld",
                "severity": "low",
                "reason": (
                    "Synthetic adjudication confirms that this reviewer finding reached "
                    "the Judge corpus."
                ),
                "evidence": [
                    {
                        "file": cited_file,
                        "observation": f"{identifier} reached adjudication",
                        "role": "verification",
                    }
                ],
            }
        )
    request.output[claims_field] = claims
    request.output["verdict"] = {
        "intent": "Synthetic run: the change under review was not inspected by a model.",
        "summary": (
            "Synthetic reviewer evidence reached adjudication; the pipeline completed "
            "without model execution."
            if claims
            else "Synthetic reviewer evidence was unavailable, so adjudication cannot proceed."
        ),
    }
    return request.output


def _remediation_shape_fields(request: GateRequest) -> tuple[str, str]:
    findings_field, remediation_field = request.field_names(
        slots=("findings", "remediation"), defaults=("findings", "remediation")
    )
    return findings_field, remediation_field


_MAX_REMEDIATION_NOTE_LENGTH = 200


def _check_diagnostics(path: str, value: Any) -> list[Diagnostic]:
    if not isinstance(value, list):
        return [Diagnostic(path, "must be an array")]
    if not value:
        return [Diagnostic(path, "must contain at least one entry")]
    diags: list[Diagnostic] = []
    for index, entry in enumerate(value):
        diags.extend(_check_entry_diagnostics(f"{path}[{index}]", entry))
    return diags


def _check_entry_diagnostics(path: str, value: Any) -> list[Diagnostic]:
    if not isinstance(value, dict):
        return [Diagnostic(path, "must be an object")]
    diags = []
    if not nonempty_str(value.get("filePath", MISSING)):
        diags.append(Diagnostic(f"{path}.filePath", "must be a non-empty string"))
    start = value.get("startLine", MISSING)
    end = value.get("endLine", MISSING)
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        diags.append(Diagnostic(f"{path}.startLine", "must be a positive integer"))
    if isinstance(end, bool) or not isinstance(end, int) or end < 1:
        diags.append(Diagnostic(f"{path}.endLine", "must be a positive integer"))
    elif isinstance(start, int) and not isinstance(start, bool) and end < start:
        diags.append(Diagnostic(f"{path}.endLine", "must not precede startLine"))
    observation = value.get("observation", MISSING)
    if not nonempty_str(observation):
        diags.append(Diagnostic(f"{path}.observation", "must be a non-empty string"))
    elif len(observation) > _MAX_REMEDIATION_NOTE_LENGTH:
        diags.append(
            Diagnostic(
                f"{path}.observation",
                f"must be at most {_MAX_REMEDIATION_NOTE_LENGTH} characters",
            )
        )
    return diags


def _limitation_diagnostics(path: str, value: Any) -> list[Diagnostic]:
    if not isinstance(value, list):
        return [Diagnostic(path, "must be an array")]
    diags = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not nonempty_str(item):
            diags.append(Diagnostic(item_path, "must be a non-empty string"))
        elif len(item) > _MAX_REMEDIATION_NOTE_LENGTH:
            diags.append(
                Diagnostic(
                    item_path,
                    f"must be at most {_MAX_REMEDIATION_NOTE_LENGTH} characters",
                )
            )
    return diags


def _remediation_diagnostics(path: str, remediation: Any) -> list[Diagnostic]:
    """Validate one finding's non-applyable remediation draft."""
    if remediation is MISSING or remediation is None:
        return [Diagnostic(f"{path}.remediation", "missing required 'remediation'")]
    if not isinstance(remediation, dict):
        return [Diagnostic(f"{path}.remediation", "'remediation' must be an object")]
    rp = f"{path}.remediation"
    diags: list[Diagnostic] = []
    for field in ("rationale", "proposal", "illustration"):
        if not nonempty_str(remediation.get(field, MISSING)):
            diags.append(Diagnostic(f"{rp}.{field}", "must be a non-empty string"))
    diags.extend(_check_diagnostics(f"{rp}.checks", remediation.get("checks", MISSING)))
    diags.extend(
        _limitation_diagnostics(f"{rp}.limitations", remediation.get("limitations", MISSING))
    )
    return diags


def remediation_draft_shape_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Every finding must carry the complete Buddies remediation draft."""
    findings_field, remediation_field = _remediation_shape_fields(request)
    diags: list[Diagnostic] = []
    for i, finding in request.findings(findings_field):
        diags.extend(
            _remediation_diagnostics(
                f"{findings_field}[{i}]",
                finding.get(remediation_field, MISSING),
            )
        )
    return diags


_DRAFT_COMPONENT = re.compile(
    r"^(?:rationale|proposal|illustration|language|"
    r"checks\[(\d+)\]\.(filePath|startLine|endLine|observation)|"
    r"limitations\[(\d+)\])$"
)


def _json_objects(values: Any) -> list[dict[str, Any]]:
    import json

    if not isinstance(values, dict):
        return []
    parsed = []
    for raw in values.values():
        if not isinstance(raw, str):
            continue
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            parsed.append(value)
    return parsed


def _eligible_remediations(request: GateRequest) -> dict[tuple[str, str], dict[str, Any]]:
    for payload in _json_objects(request.context.get("upstream_responses")):
        rows = payload.get("eligible_remediations")
        if not isinstance(rows, list):
            continue
        eligible = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            claim = row.get("claim")
            claim_id = claim.get("id") if isinstance(claim, dict) else None
            source_id = row.get("primary_source_finding_id")
            if isinstance(claim_id, str) and isinstance(source_id, str):
                eligible[(claim_id, source_id)] = row
        return eligible
    return {}


def _component_value(remediation: Any, component: Any) -> str | None:
    if not isinstance(remediation, dict) or not isinstance(component, str):
        return None
    match = _DRAFT_COMPONENT.fullmatch(component)
    if match is None:
        return None
    if match.group(1) is not None:
        checks = remediation.get("checks")
        index = int(match.group(1))
        value = (
            checks[index].get(match.group(2))
            if isinstance(checks, list) and index < len(checks) and isinstance(checks[index], dict)
            else None
        )
    elif match.group(3) is not None:
        limitations = remediation.get("limitations")
        index = int(match.group(3))
        value = (
            limitations[index]
            if isinstance(limitations, list) and index < len(limitations)
            else None
        )
    else:
        value = remediation.get(component)
    return str(value) if isinstance(value, str | int) and not isinstance(value, bool) else None


def _draft_excerpt_is_grounded(item: dict[str, Any], dossier: dict[str, Any]) -> bool:
    finding = dossier.get("reviewer_finding")
    remediation = finding.get("remediation") if isinstance(finding, dict) else None
    value = _component_value(remediation, item.get("component"))
    excerpt = item.get("excerpt")
    return isinstance(excerpt, str) and value is not None and excerpt in value


def _path_in_args(path: str, args: Any) -> bool:
    if not isinstance(args, dict):
        return False
    return any(
        isinstance(value, str) and value.replace("\\", "/").endswith(path.replace("\\", "/"))
        for key, value in args.items()
        if key in {"path", "file", "filePath"}
    )


def _exact_view_range(item: dict[str, Any], args: Any) -> bool:
    if not isinstance(args, dict):
        return False
    view_range = args.get("view_range")
    return (
        isinstance(view_range, list)
        and len(view_range) == 2
        and view_range[0] == item.get("start_line")
        and view_range[1] == item.get("end_line")
    )


def _repository_excerpt_is_grounded(item: dict[str, Any], request: GateRequest) -> bool:
    path = item.get("path")
    excerpt = item.get("excerpt")
    if not isinstance(path, str) or not isinstance(excerpt, str):
        return False
    for call in request.context.get("tool_calls") or ():
        if not isinstance(call, dict) or call.get("ok") is not True:
            continue
        if call.get("name") != "view":
            continue
        output = call.get("output")
        if not isinstance(output, str) or excerpt not in output:
            continue
        args = call.get("args")
        if _path_in_args(path, args) and _exact_view_range(item, args):
            return True
    return False


def _evidence_diagnostics(
    path: str, decision: dict[str, Any], dossier: dict[str, Any], request: GateRequest
) -> list[Diagnostic]:
    evidence = decision.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return [Diagnostic(f"{path}.evidence", "must contain evidence")]
    diags = []
    has_repository = False
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            continue
        item_path = f"{path}.evidence[{index}]"
        source = item.get("source")
        if source == "draft" and not _draft_excerpt_is_grounded(item, dossier):
            diags.append(
                Diagnostic(
                    f"{item_path}.excerpt",
                    "must occur in the cited draft component",
                )
            )
        if source == "repository":
            has_repository = True
            start, end = item.get("start_line"), item.get("end_line")
            if isinstance(start, int) and isinstance(end, int):
                if end < start:
                    diags.append(
                        Diagnostic(
                            f"{item_path}.end_line",
                            "must not precede start_line",
                        )
                    )
                elif end - start > 20:
                    diags.append(
                        Diagnostic(
                            item_path,
                            "repository excerpt must span at most 20 lines",
                        )
                    )
            if not _repository_excerpt_is_grounded(item, request):
                diags.append(
                    Diagnostic(
                        f"{item_path}.excerpt",
                        "must occur in a successful exact-range view of the cited repository lines",
                    )
                )
    if decision.get("decision") == "publish" and not has_repository:
        diags.append(
            Diagnostic(
                f"{path}.evidence",
                "a publish decision requires independently corroborated repository evidence",
            )
        )
    return diags


def remediation_decisions_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Require one grounded decision for every supplied eligible remediation."""
    if not isinstance(request.context.get("upstream_responses"), dict):
        return []
    eligible = _eligible_remediations(request)
    decisions = request.output.get("decisions")
    if not isinstance(decisions, list):
        return []
    seen: set[tuple[str, str]] = set()
    diags: list[Diagnostic] = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            continue
        path = f"decisions[{index}]"
        claim_id = decision.get("claim_id")
        source_id = decision.get("primary_source_finding_id")
        if not isinstance(claim_id, str) or not isinstance(source_id, str):
            continue
        pair = (claim_id, source_id)
        if pair in seen:
            diags.append(Diagnostic(path, f"duplicate remediation decision for {pair!r}"))
            continue
        seen.add(pair)
        dossier = eligible.get(pair)
        if dossier is None:
            diags.append(Diagnostic(path, "does not reference a supplied eligible remediation"))
            continue
        if not nonempty_str(decision.get("reason", MISSING)):
            diags.append(Diagnostic(f"{path}.reason", "must be a non-empty material reason"))
        diags.extend(_evidence_diagnostics(path, decision, dossier, request))
    for pair in sorted(set(eligible) - seen):
        diags.append(Diagnostic("decisions", f"missing remediation decision for {pair!r}"))
    return diags


#: Tool names that count as executing a command, and command patterns that count as a
#: test/coverage run. Engine defaults — a config may override both via per-agent
#: ``params`` in ``agent_graph.yaml`` (the runner-pattern list is config-owned).
_DEFAULT_RUNNER_TOOLS: tuple[str, ...] = ("shell", "bash", "powershell", "execute")


_DEFAULT_RUNNER_PATTERNS: tuple[str, ...] = (
    r"(?:^|[;&|]\s*)(?:(?:python|py)\s+-m\s+)?pytest\b",
    r"(?:^|[;&|]\s*)(?:npx\s+)?jest\b",
    r"(?:^|[;&|]\s*)(?:npx\s+)?vitest\b",
    r"(?:^|[;&|]\s*)(?:npx\s+)?mocha\b",
    r"(?:^|[;&|]\s*)dotnet\s+test\b",
    r"(?:^|[;&|]\s*)go\s+test\b",
    r"(?:^|[;&|]\s*)cargo\s+test\b",
    r"(?:^|[;&|]\s*)npm\s+(?:run\s+)?test\b",
    r"(?:^|[;&|]\s*)yarn\s+test\b",
    r"(?:^|[;&|]\s*)pnpm\s+test\b",
    r"(?:^|[;&|]\s*)(?:(?:python|py)\s+-m\s+)?coverage\s+(?:run|report|html|xml)\b",
)


def _evidence_field(request: GateRequest) -> str:
    """Resolve the finding's evidence-class field (``findings[].evidence``)."""
    _, evidence_field = request.field_names(
        slots=("findings", "evidence"), defaults=("findings", "evidence")
    )
    return evidence_field


def _command_text(args: Any) -> str:
    """The actual command submitted to a runner tool, excluding descriptive metadata."""
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        command = args.get("command")
        return command if isinstance(command, str) else ""
    return "" if args is None else str(args)


def _execution_bound(tool_calls: Any, runner_tools: Sequence[str], patterns: Sequence[str]) -> bool:
    """True iff the agent made ≥1 SUCCESSFUL runner-tool call whose command matches a
    runner pattern — the per-agent binding for a ``measured`` evidence claim."""
    if not isinstance(tool_calls, list):
        return False
    tools = {t.lower() for t in runner_tools}
    for call in tool_calls:
        if not isinstance(call, dict) or call.get("ok") is not True:
            continue
        name = call.get("name")
        if not isinstance(name, str) or name.lower() not in tools:
            continue
        text = _command_text(call.get("args"))
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            return True
    return False


def execution_backed_evidence_gate(request: GateRequest, /) -> list[Diagnostic]:
    """A finding that claims ``measured`` evidence must be backed by a real run.

    E-FAITH guard: an agent's fluent "measured" claim is not proof it executed anything.
    Binds the claim to telemetry — a successful runner tool call
    (``context['tool_calls']`` entries ``{name, args, ok}``) whose command matches a
    config-owned runner pattern. Granularity is per-agent (telemetry is not per-finding):
    ONE bound run covers every ``measured`` claim by this agent; it does not attest
    each finding independently. If the agent ran nothing matching, each ``measured``
    finding is flagged so the graph's ERROR override retries it as ``inferred``.
    Neutral diagnostics; the manifest default remains WARN for consumers without that
    strict policy. Field-name-agnostic via ``requires``; absent telemetry is unbound.
    """
    measured_value = request.params.get("measured_value", "measured")
    runner_tools = request.params.get("runner_tools", _DEFAULT_RUNNER_TOOLS)
    runner_patterns = request.params.get("runner_patterns", _DEFAULT_RUNNER_PATTERNS)
    evidence_field = _evidence_field(request)
    if _execution_bound(request.context.get("tool_calls"), runner_tools, runner_patterns):
        return []
    target = measured_value.strip().lower()
    diags: list[Diagnostic] = []
    for i, finding in request.findings():
        value = js_str(nullish(finding.get(evidence_field, MISSING), "")).strip().lower()
        if value == target:
            diags.append(
                Diagnostic(
                    f"findings[{i}].{evidence_field}",
                    f"claims {measured_value!r} evidence but no successful test/coverage run "
                    "was recorded for this agent — treat as inferred",
                )
            )
    return diags


def claim_consistency_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Require a claim's disposition and severity to name one legal adjudication cell."""
    claims_f = "claims"
    if not isinstance(request.output.get(claims_f), list):
        return []  # container shape is the json_schema gate's call, not this one's

    claims = [c for c in request.output[claims_f] if isinstance(c, dict)]
    diags: list[Diagnostic] = []
    for i, claim in enumerate(claims):
        disposition = claim.get("disposition")
        rulings = RULING_MATRIX.get(str(disposition))
        if rulings is None:
            continue  # unrecognized disposition is the json_schema gate's call
        severity = claim.get("severity")
        if severity not in rulings:
            diags.append(
                Diagnostic(
                    f"{claims_f}[{i}].severity",
                    f"must be one of {sorted(rulings)!r} for disposition {disposition!r}",
                )
            )
    return diags


# --- registration: each name matches an `fn:` in this bundle's gates.yaml ---
register_gate_function("claims_coverage", claims_coverage_gate)
register_simulation_transform("claims_coverage", claims_coverage_simulation)
register_gate_function("primary_source_selection", primary_source_selection_gate)
register_gate_function("remediation_draft_shape", remediation_draft_shape_gate)
register_gate_function("remediation_decisions", remediation_decisions_gate)
register_gate_function("execution_backed_evidence", execution_backed_evidence_gate)
register_gate_function("claim_consistency", claim_consistency_gate)

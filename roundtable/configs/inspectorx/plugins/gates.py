"""gates: the InspectorX review bundle's OVG gate callables.

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

What makes these seven InspectorX-specific rather than engine-general: they read
this bundle's own finding vocabulary — ``locations[]``, ``exploitability``/``trace``
evidence depth, the ``fix`` union, a ``mode`` selector, and the Judge's overlay
entry shape — none of which another review config is obliged to share. The two
advisory ones (``generic_phrase``, ``fix_prose_shape``) additionally encode
heuristics (a boilerplate phrase list, a Markdown-rendering hazard) that are
judgment calls belonging to a config, not invariants belonging to an engine.

Each ``register_gate_function`` name must match an ``fn:`` in ``configs/inspectorx/
gates.yaml``; an unregistered ``fn`` fails ``load_gate_registry`` loudly rather than
silently disabling a gate.
"""

from __future__ import annotations

import re
from typing import Any

from roundtable.plugins import register_gate_function
from roundtable.types import exploitability_ratings, severity_rank
from roundtable.validation import (
    MISSING,
    Diagnostic,
    GateRequest,
    is_js_integer,
    is_js_number,
    js_str,
    js_typeof,
    nullish,
)

_BOILERPLATE_PHRASES = (
    "consider adding null checks",
    "add error handling",
    "improve readability",
    "follow naming conventions",
    "add documentation",
)


def generic_phrase_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Heuristic gate: flag boilerplate finding descriptions.

    A closed phrase blocklist is gameable, so this is advisory-only (the runner
    stamps it ``warn``). The blocklist is a heuristic literal (formatting logic),
    not an output contract; it may also be supplied via ``params['phrases']``.
    """
    phrases = request.params.get("phrases") or _BOILERPLATE_PHRASES
    diags: list[Diagnostic] = []
    for i, finding in request.findings():
        src = finding.get("ideal_code_would")
        if src is None:
            src = finding.get("description")
        desc = (src if isinstance(src, str) else "").lower()
        if any(p in desc for p in phrases):
            diags.append(
                Diagnostic(
                    path=f"findings[{i}].description",
                    message=f"description appears generic/boilerplate: {desc[:60]!r}",
                )
            )
    return diags


# A line Markdown renders as an indented code block: starts (column 0) with a tab or
# 4+ spaces followed by non-whitespace. Used to catch raw code smuggled into a prose
# ``fix`` string (the fence check misses unfenced, indented snippets).
_INDENTED_CODE_LINE = re.compile(r"(?m)^(?:\t| {4,})\S")


def _location_diagnostics(path: str, finding: dict[str, Any]) -> list[Diagnostic]:
    """Neutral structural check of one finding's ``locations[]`` (presence, item shape,
    integer≥1, ``endLine≥startLine``)."""
    locs = finding.get("locations", MISSING)
    if locs is MISSING or locs is None:
        if isinstance(finding.get("location"), str):
            return [
                Diagnostic(
                    f"{path}.location",
                    "uses legacy 'location' string — must emit structured 'locations[]' "
                    "(filePath/startLine/endLine)",
                )
            ]
        return [Diagnostic(f"{path}.locations", "missing required 'locations[]' array")]
    if not isinstance(locs, list):
        return [Diagnostic(f"{path}.locations", f"must be array, got {js_typeof(locs)}")]
    if len(locs) == 0:
        return [Diagnostic(f"{path}.locations", "must have \u22651 entry")]

    diags: list[Diagnostic] = []
    for i, loc in enumerate(locs):
        lp = f"{path}.locations[{i}]"
        if not isinstance(loc, dict):
            diags.append(Diagnostic(lp, "must be object with filePath/startLine/endLine"))
            continue
        file_path = loc.get("filePath", MISSING)
        start_line = loc.get("startLine", MISSING)
        end_line = loc.get("endLine", MISSING)
        if not isinstance(file_path, str) or len(file_path) == 0:
            diags.append(Diagnostic(f"{lp}.filePath", "must be non-empty string"))
        if not is_js_number(start_line) or not is_js_integer(start_line) or start_line < 1:
            diags.append(Diagnostic(f"{lp}.startLine", "must be integer \u22651"))
        if not is_js_number(end_line) or not is_js_integer(end_line) or end_line < 1:
            diags.append(Diagnostic(f"{lp}.endLine", "must be integer \u22651"))
        if (
            is_js_number(start_line)
            and is_js_number(end_line)
            and is_js_integer(start_line)
            and is_js_integer(end_line)
            and end_line < start_line
        ):
            diags.append(
                Diagnostic(
                    f"{lp}.endLine",
                    f"endLine ({js_str(end_line)}) must be \u2265 startLine ({js_str(start_line)})",
                )
            )
    return diags


def locations_floor_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Every finding must carry a structurally-valid grounded ``locations[]``.

    Neutral: the runner stamps ``error`` for the specialist agents that hard-enforce
    locations and ``warn`` for the baseline finding agents. Owns the whole locations[]
    structural contract (presence, item shape, line integers, ``endLine≥startLine``) in
    one place, so the per-agent schema does not need to duplicate any of it.
    """
    diags: list[Diagnostic] = []
    for i, finding in request.findings():
        diags.extend(_location_diagnostics(f"findings[{i}]", finding))
    return diags


def _normalize_exploitability_rating(raw: Any, configuration) -> str | None:
    if not isinstance(raw, str):
        return None
    v = raw.strip().lower()
    return v if v in exploitability_ratings(configuration) else None


def _proof_diagnostics(
    path: str, finding: dict[str, Any], *, min_steps: int, configuration
) -> list[Diagnostic]:
    """Block-level evidence floor for high-severity findings.

    High-severity findings must justify themselves (exploitability rating+reasoning,
    a ``trace`` of ≥ ``min_steps`` reproduction/execution steps); every finding owes a
    non-empty ``impact``. The *fix* requirement is owned solely by the ``fix_present``
    gate — this gate only guards evidence depth. This gate is error-only: a warn-level
    advisory would force one gate to span two levels and violate the one-gate-one-level
    invariant.
    """
    min_steps = max(1, min_steps)
    sev = js_str(nullish(finding.get("severity", MISSING), "")).upper()
    is_high = sev in ("CRITICAL", "HIGH")
    diags: list[Diagnostic] = []

    exp = finding.get("exploitability", MISSING)
    if is_high:
        if exp is MISSING or exp is None:
            diags.append(Diagnostic(f"{path}.exploitability", "missing 'exploitability'"))
        elif not isinstance(exp, dict):
            diags.append(Diagnostic(f"{path}.exploitability", "must be object"))
        else:
            if not _normalize_exploitability_rating(exp.get("rating", MISSING), configuration):
                diags.append(
                    Diagnostic(
                        f"{path}.exploitability.rating",
                        f"must be one of "
                        f"{sorted(exploitability_ratings(configuration))} (case-insensitive)",
                    )
                )
            reasoning = exp.get("reasoning", MISSING)
            if not isinstance(reasoning, str) or reasoning.strip() == "":
                diags.append(
                    Diagnostic(f"{path}.exploitability.reasoning", "must be non-empty string")
                )
    elif exp is not MISSING and exp is not None and not isinstance(exp, dict):
        diags.append(Diagnostic(f"{path}.exploitability", "must be object when present"))

    steps = finding.get("trace", MISSING)
    if is_high:
        if not isinstance(steps, list) or len(steps) == 0:
            diags.append(Diagnostic(f"{path}.trace", "missing non-empty 'trace' array"))
        elif len(steps) < min_steps:
            diags.append(
                Diagnostic(
                    f"{path}.trace",
                    f"must have at least {min_steps} steps (got {len(steps)})",
                )
            )
    elif steps is not MISSING and steps is not None and not isinstance(steps, list):
        diags.append(Diagnostic(f"{path}.trace", "must be array when present"))

    impact = finding.get("impact", MISSING)
    if not isinstance(impact, str) or impact.strip() == "":
        diags.append(Diagnostic(f"{path}.impact", "missing non-empty 'impact'"))

    return diags


def proof_depth_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Evidence-depth floor for high-severity findings (security / schema_drift).

    Parameterized + reusable: ``min_steps`` comes from the agent's per-gate ``params``
    in ``agent_graph.yaml``, so two agents with different depth requirements share this
    one function. The fix requirement is owned by ``fix_present``, not here.
    """
    min_steps = request.params.get("min_steps", 1)
    if request.configuration is None:
        raise ValueError("proof_depth gate requires the run Configuration")
    diags: list[Diagnostic] = []
    for i, finding in request.findings():
        diags.extend(
            _proof_diagnostics(
                f"findings[{i}]",
                finding,
                min_steps=min_steps,
                configuration=request.configuration,
            )
        )
    return diags


def _fix_diagnostics(path: str, finding: dict[str, Any], configuration) -> list[Diagnostic]:
    """A real defect (severity ≥ low) owes a well-formed, present ``fix``.

    ``fix`` is the canonical union term: a ``{language, code}`` one-click suggestion OR a
    precise prose instruction. This gate enforces *presence + shape* — the part a
    deterministic gate can decide completely: a fix must exist, be non-empty, and be
    either a well-formed suggestion object or a non-empty string. *Quality* (is the fix
    concrete rather than boilerplate?) is a semantic judgment left to the agent prompt
    and the advisory ``generic_phrase`` gate, not hard-failed here on a partial blocklist.
    ``info`` (and unknown-severity) findings are exempt, mirroring ``severity_blocking``.
    """
    sev = js_str(nullish(finding.get("severity", MISSING), "")).strip().lower()
    if severity_rank(configuration, sev) < severity_rank(configuration, "low"):
        return []

    fix = finding.get("fix", MISSING)
    if fix is MISSING or fix is None:
        return [Diagnostic(f"{path}.fix", "missing required 'fix' for a severity>=low finding")]

    if isinstance(fix, dict):
        diags: list[Diagnostic] = []
        lang = fix.get("language")
        code = fix.get("code")
        if not isinstance(lang, str) or not lang.strip():
            diags.append(
                Diagnostic(
                    f"{path}.fix.language", "suggestion 'fix' must carry a non-empty 'language'"
                )
            )
        if not isinstance(code, str) or not code.strip():
            diags.append(
                Diagnostic(f"{path}.fix.code", "suggestion 'fix' must carry a non-empty 'code'")
            )
        return diags

    if isinstance(fix, str):
        if not fix.strip():
            return [Diagnostic(f"{path}.fix", "empty 'fix' string")]
        return []

    return [
        Diagnostic(
            f"{path}.fix",
            "'fix' must be a {language, code} suggestion object or a precise prose string",
        )
    ]


def fix_present_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Every real defect (severity ≥ low) must ship one present, well-formed ``fix``.

    Single owner of the fix *presence* requirement across finding agents (subsumes the
    old ``proof_depth.require_fix``). Neutral diagnostics; the runner stamps ``error``.
    Fix *quality* is not judged here — see ``_fix_diagnostics``.
    """
    diags: list[Diagnostic] = []
    if request.configuration is None:
        raise ValueError("fix_present gate requires the run Configuration")
    for i, finding in request.findings():
        diags.extend(_fix_diagnostics(f"findings[{i}]", finding, request.configuration))
    return diags


def fix_prose_shape_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Advisory: a prose (string) ``fix`` must not embed code — fenced or indented.

    The canonical ``fix`` contract is a ``{language, code}`` suggestion object for
    one-click code OR a plain prose instruction — never code smuggled into the string
    form. Two shapes break rendering:

    * a Markdown code fence (```` ``` ````) inside the prose — defanged by the comment
      renderer (fences are neutralized to prevent layout breaks / watermark forgery),
      so it renders as a broken block; and
    * a **tab/4-space-indented** line — Markdown renders it as an indented code block,
      so a multi-line snippet lands half inside a code block and half as prose (the
      failure that motivated this check).

    Flag either so the agent emits a structured ``{language, code}`` suggestion object
    instead. Non-blocking (warn); the renderer still degrades it safely. Neutral
    diagnostics; the runner stamps the level.
    """
    diags: list[Diagnostic] = []
    for i, finding in request.findings():
        fix = finding.get("fix", MISSING)
        if not isinstance(fix, str):
            continue
        if "```" in fix:
            reason = "a Markdown code fence (```)"
        elif _INDENTED_CODE_LINE.search(fix):
            reason = "a tab/4-space-indented line Markdown renders as a code block"
        else:
            continue
        diags.append(
            Diagnostic(
                f"findings[{i}].fix",
                f"prose 'fix' embeds {reason}; emit a {{language, code}} suggestion "
                "object for one-click code instead of putting code in the string form",
            )
        )
    return diags


def mode_dependent_arrays_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Conditional presence: given a mode selector, certain fields must be non-empty
    arrays. Case-INSENSITIVE on the mode value (``mode.lower()``), which is why this is a
    gate and not JSON-Schema ``if``/``then`` (draft 2020-12 ``const`` is case-sensitive).

    ``requirements`` maps a lower-cased mode value to the list of fields it demands, e.g.
    ``{standard: [happy_path_traces], inverted: [breakage_report]}`` — pure declarative
    config passed from ``agent_graph.yaml``, no field names baked into code.
    """
    mode_field = request.params.get("mode_field", "mode")
    reqs = request.params.get("requirements") or {}
    mode = js_str(nullish(request.output.get(mode_field, MISSING), "")).lower()
    required_fields = reqs.get(mode) or []
    diags: list[Diagnostic] = []
    for field_name in required_fields:
        value = request.output.get(field_name, MISSING)
        if not isinstance(value, list) or len(value) == 0:
            diags.append(
                Diagnostic(
                    field_name,
                    f"must be a non-empty array when {mode_field}={mode!r}",
                )
            )
    return diags


def _judge_nonempty_str(entry: dict[str, Any], key: str, path: str) -> list[Diagnostic]:
    val = entry.get(key, MISSING)
    if not isinstance(val, str) or val.strip() == "":
        return [Diagnostic(f"{path}.{key}", "must be non-empty string")]
    return []


def judge_entries_gate(request: GateRequest, /) -> list[Diagnostic]:
    """Deep per-entry validation for the Judge overlay/reference/observation arrays.

    Judge-specific (like ``proof_depth`` is finding-specific): it owns the structural
    per-entry contract — required non-empty ids, boolean flags, the ``JO-<n>`` id
    pattern, nested ``merged_with`` refs, and the payload fields that are prohibited on
    overlay entries. Field lists come from declarative ``params`` in ``agent_graph.yaml``.
    The severity **enums** are NOT owned here: ``verdict_severity`` is typed by
    ``$ref severity_blocking`` and ``judge_observations[].severity`` by ``$ref severity`` in
    ``judge.schema.yaml`` and enforced by the ``json_schema`` gate — the schema owns
    value-types/enums, this gate owns the structural/cross-field rules.
    """
    output = request.output
    prohibited = request.params.get("prohibited_payload_fields") or []
    observation_id_pattern = request.params.get("observation_id_pattern", r"^JO-\d+$")
    id_re = re.compile(observation_id_pattern)
    diags: list[Diagnostic] = []

    overlay = output.get("verdict_overlay")
    if isinstance(overlay, list):
        for i, entry in enumerate(overlay):
            if not isinstance(entry, dict):
                continue
            path = f"verdict_overlay[{i}]"
            diags += _judge_nonempty_str(entry, "finding_id", path)
            diags += _judge_nonempty_str(entry, "source_agent", path)
            if not isinstance(entry.get("blocking", MISSING), bool):
                diags.append(Diagnostic(f"{path}.blocking", "must be boolean"))
            diags += _judge_nonempty_str(entry, "judge_justification", path)

            mw = entry.get("merged_with")
            if "merged_with" in entry and mw is not None:
                if not isinstance(mw, list):
                    diags.append(Diagnostic(f"{path}.merged_with", "must be array when present"))
                else:
                    for j, ref in enumerate(mw):
                        rp = f"{path}.merged_with[{j}]"
                        if not isinstance(ref, dict):
                            diags.append(
                                Diagnostic(rp, "must be {source_agent, finding_id} object")
                            )
                            continue
                        diags += _judge_nonempty_str(ref, "source_agent", rp)
                        diags += _judge_nonempty_str(ref, "finding_id", rp)

            for f in prohibited:
                if f in entry:
                    diags.append(
                        Diagnostic(
                            f"{path}.{f}",
                            "is PROHIBITED on verdict_overlay entries — Judge references "
                            "findings by (source_agent, finding_id); the publisher renders "
                            "the payload",
                        )
                    )

    for bucket in ("validated_safe", "needs_human_judgment"):
        arr = output.get(bucket)
        if isinstance(arr, list):
            for i, entry in enumerate(arr):
                if not isinstance(entry, dict):
                    continue
                path = f"{bucket}[{i}]"
                diags += _judge_nonempty_str(entry, "finding_id", path)
                diags += _judge_nonempty_str(entry, "source_agent", path)
                diags += _judge_nonempty_str(entry, "reason", path)

    observations = output.get("judge_observations")
    if isinstance(observations, list):
        for i, entry in enumerate(observations):
            if not isinstance(entry, dict):
                continue
            path = f"judge_observations[{i}]"
            oid = entry.get("id", MISSING)
            if not isinstance(oid, str) or not id_re.match(oid):
                diags.append(Diagnostic(f"{path}.id", f"must match /{observation_id_pattern}/"))
            diags += _judge_nonempty_str(entry, "summary", path)
            if not isinstance(entry.get("blocking", MISSING), bool):
                diags.append(Diagnostic(f"{path}.blocking", "must be boolean"))

    return diags


# --- registration: each name matches an `fn:` in this bundle's gates.yaml ---
register_gate_function("generic_phrase", generic_phrase_gate)
register_gate_function("locations_floor", locations_floor_gate)
register_gate_function("proof_depth", proof_depth_gate)
register_gate_function("fix_present", fix_present_gate)
register_gate_function("fix_prose_shape", fix_prose_shape_gate)
register_gate_function("mode_dependent_arrays", mode_dependent_arrays_gate)
register_gate_function("judge_entries", judge_entries_gate)

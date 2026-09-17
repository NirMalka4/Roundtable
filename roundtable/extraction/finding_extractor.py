"""finding_extractor: parse an agent's JSON output into individual findings.

WHICH arrays are findings is **not** hardcoded here — each agent's output schema
marks its finding array with ``x-finding-array``. By default a finding item reads
the **canonical vocabulary** field of each name (``id``, ``description``,
``severity``, ``category``, ``locations``, …). An array whose rows deviate from
that shape declares an ``x-finding-adapter`` right next to the marker, naming the
source key for a role (e.g. ``id: finding_id``), a value ``map`` for severity, a
row ``filter``, or per-field ``defaults``. There is no cross-agent alias table:
each deviation lives in the one schema that needs it, so the extraction contract
has a single source of truth (the schemas), kept honest by doctor.

``extract_findings`` preserves raw-collection order exactly so the derived
finding-id set (and thus the index keys + ``count_verification`` totals) is
deterministic across runs.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from roundtable.utils import extract_json
from roundtable.validation import MISSING, is_js_number, js_str

if TYPE_CHECKING:
    from roundtable.graph import Configuration

_TRAILING_PUNCT_RE = re.compile(r"[\s.,;:!?]+$")


@dataclass(frozen=True)
class NormalizedLocation:
    """A single anchored code location."""

    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True)
class CodeBlock:
    """A structured one-click suggestion block."""

    language: str
    code: str


@dataclass(frozen=True)
class RemediationCheck:
    """One code-reading observation used to challenge a proposed remediation."""

    location: NormalizedLocation
    observation: str


@dataclass(frozen=True)
class Remediation:
    """A remediation parsed from either the legacy union or the Buddies draft shape.

    Legacy consumers retain exactly the three existing shapes:

    * ``suggestion`` — a drop-in ``replacement`` for the lines of ``target`` (the
      anchor resolved from the emitted ``anchorIndex`` at extraction, so publish can
      attach a one-click ``suggestion`` fence to that exact span regardless of later
      cross-agent location merging).
    * ``fix`` — ``prose`` markdown for a cross-file / architectural change that has no
      single drop-in line range. Prose carries no code: illustrative code is a ``draft``.
    * ``draft`` — illustrative ``code`` (optionally ``language``-tagged) that is NOT a
      drop-in for the anchored span — a net-new test, a sketch of a helper, a sample
      config. Rendered as a plain fence, **never** a one-click ``suggestion`` (applying
      it would overwrite the wrong lines).

    Buddies uses the non-applyable ``proposal`` + ``illustration`` shape, represented
    with ``kind=None``. ``checks`` records reviewer investigation seeds and
    ``limitations`` discloses known gaps. Neither implies execution.
    """

    kind: str | None = None  # legacy: 'suggestion' | 'fix' | 'draft'
    rationale: str | None = None
    proposal: str | None = None
    illustration: str | None = None
    replacement: str | None = None
    target: NormalizedLocation | None = None
    prose: str | None = None
    code: str | None = None
    language: str | None = None
    checks: tuple[RemediationCheck, ...] | None = None
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Exploitability:
    """A grounding qualifier: how reachable/weaponizable the defect is."""

    rating: str
    reasoning: str | None = None


@dataclass
class FindingItem:
    """A single extracted finding.

    The first block is the index/overlay-parity surface (id, severity, category,
    judge_category, file, line, description). The second block is the **publish**
    payload (short ``title``, ``line_end``, structured ``locations``, ``evidence``).
    The third block is the **grounding** payload that lets a published comment ground
    its claim and conclude with a fix: the canonical ``fix`` (one-click
    :class:`CodeBlock` or prose), an ordered ``trace`` (the execution path / chain), an
    ``impact`` conclusion and an ``exploitability`` qualifier. All publish/grounding
    fields are optional and never influence the index/overlay surface.
    """

    agent_name: str
    id: str
    severity: str
    category: str
    judge_category: str | None = None
    file: str | None = None
    line: int | None = None
    description: str = ""
    # ── Publish payload ──
    title: str | None = None
    line_end: int | None = None
    locations: tuple[NormalizedLocation, ...] | None = None
    evidence: tuple[str, ...] = ()
    # ── Grounding payload (canonical vocabulary: fix / trace / impact / exploitability) ──
    fix: CodeBlock | str | None = None
    remediation: Remediation | None = None
    trace: tuple[str, ...] = ()
    impact: str | None = None
    exploitability: Exploitability | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Per-property adapter (declared in the schema next to ``x-finding-array``)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Adapter:
    """How one finding-array property's rows map onto the canonical finding.

    Every field defaults to reading the canonical key of the same name; a schema
    overrides only what deviates. ``filter`` drops rows before collection;
    ``severity_map`` translates a source verdict into a canonical severity;
    ``defaults`` supply severity/category when the row omits them.
    """

    id_key: str = "id"
    description_key: str = "description"
    title_key: str = "title"
    severity_key: str = "severity"
    category_key: str = "category"
    severity_map: Mapping[str, str] = field(default_factory=dict)
    filter_field: str | None = None
    filter_exclude: tuple[Any, ...] = ()
    default_severity: str = "info"
    default_category: str = "unknown"
    # Where a finding's evidence array lives and how its rows spell file/line, so a
    # schema may name the field/sub-keys to match its native contract (e.g. ``anchors``)
    # via ``x-finding-adapter.locations`` without the extractor hard-coding one spelling.
    locations_key: str = "locations"
    location_file_key: str = "filePath"
    location_start_key: str = "startLine"
    location_end_key: str = "endLine"


def _make_adapter(spec: Mapping[str, Any] | None) -> _Adapter:
    if not isinstance(spec, dict):
        return _Adapter()
    sev = spec.get("severity")
    severity_key, severity_map = "severity", {}
    if isinstance(sev, str):
        severity_key = sev
    elif isinstance(sev, dict):
        severity_key = sev.get("from", "severity")
        severity_map = dict(sev.get("map") or {})
    filt = spec.get("filter") or {}
    defaults = spec.get("defaults") or {}
    loc = spec.get("locations") or {}
    return _Adapter(
        id_key=spec.get("id", "id"),
        description_key=spec.get("description", "description"),
        title_key=spec.get("title", "title"),
        severity_key=severity_key,
        category_key=spec.get("category", "category"),
        severity_map=severity_map,
        filter_field=filt.get("field"),
        filter_exclude=tuple(filt.get("exclude") or ()),
        default_severity=defaults.get("severity", "info"),
        default_category=defaults.get("category", "unknown"),
        locations_key=loc.get("from", "locations"),
        location_file_key=loc.get("filePath", "filePath"),
        location_start_key=loc.get("startLine", "startLine"),
        location_end_key=loc.get("endLine", "endLine"),
    )


def _arrays_of_schema(schema: Mapping[str, Any] | None) -> dict[str, _Adapter]:
    """The ``x-finding-array`` properties of one schema, mapped to their adapters."""
    props = (schema or {}).get("properties") or {}
    return {
        name: _make_adapter(spec.get("x-finding-adapter"))
        for name, spec in props.items()
        if isinstance(spec, dict) and spec.get("x-finding-array")
    }


@cache
def _finding_arrays_by_agent(root: Path) -> Mapping[str, Mapping[str, _Adapter]]:
    """Per-agent ``{finding-array property name -> adapter}`` from that agent's own
    ``output_schema``.

    Resolution is scoped to the producing agent — not a globally-unique property
    name — so two agents may share a finding-array name (e.g. ``findings``) yet each
    keep its own adapter. ``extract_findings`` always knows which agent produced the
    response, so this is both more precise and free of the "names must be globally
    unique" invariant.
    """
    from roundtable.graph import get_configuration

    result: dict[str, Mapping[str, _Adapter]] = {}
    for entry in get_configuration(root).entries:
        if not entry.output_schema:
            continue
        try:
            schema = yaml.safe_load(
                (root / "schemas" / entry.output_schema).read_text(encoding="utf-8")
            )
        except OSError:
            continue
        arrays = _arrays_of_schema(schema)
        if arrays:
            result[entry.key] = arrays
    return result


@cache
def _finding_arrays(root: Path) -> Mapping[str, _Adapter]:
    """Fallback map of every ``x-finding-array`` property name to its adapter, across
    all schemas — used only when a response's agent key is not in the graph.

    First-writer-wins across schemas: if two agents share a property name their
    adapters would collide here, which is exactly why the primary path
    (:func:`_finding_arrays_by_agent`) scopes by producing agent instead.
    """
    arrays: dict[str, _Adapter] = {}
    for path in sorted((root / "schemas").glob("*.schema.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, adapter in _arrays_of_schema(schema).items():
            arrays.setdefault(name, adapter)
    return arrays


# ─────────────────────────────────────────────────────────────────────────────
# Field parsers (shape/type logic)
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_for_id(text: str) -> str:
    return _TRAILING_PUNCT_RE.sub("", text.lower()).strip()


def synthesize_stable_id(agent_name: str, file: str | None, line: int | None, title: str) -> str:
    """Deterministic id when a raw finding lacks an explicit id.

    Stable id: ``sha1(file:line:normTitle)[:12]`` prefixed with the agent name.
    """
    file_key = file if file is not None else ""
    line_key = str(line) if isinstance(line, int) and not isinstance(line, bool) else ""
    title_key = _normalize_for_id(title)
    digest = hashlib.sha1(f"{file_key}:{line_key}:{title_key}".encode()).hexdigest()[:12]
    return f"{agent_name}:{digest}"


def _get(obj: Any, key: str) -> Any:
    """Dict lookup returning ``MISSING`` for absent keys (``null`` stays ``None``)."""
    if isinstance(obj, dict) and key in obj:
        return obj[key]
    return MISSING


def _present(value: Any) -> bool:
    return not (value is None or value is MISSING)


def _str(raw: Mapping[str, Any], key: str, default: str) -> str:
    """JS-string render of ``raw[key]`` falling back to ``default`` when nullish."""
    value = _get(raw, key)
    return js_str(value if _present(value) else default)


def _non_empty_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and value.strip() else None


# Evidence is captured from the first non-empty of these keys (render payload only,
# never the index/overlay surface). Unifying this evidence family into one canonical
# term is intentionally deferred to the `evidence-coverage-unify` track — until then
# this small coalescing list preserves the pre-refactor behavior verbatim.
_EVIDENCE_KEYS = ("evidence", "proof", "evidence_bullets", "evidence_chain")


def _parse_evidence(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Capture supporting evidence as a tuple of bullet strings (render payload only).

    The first evidence-family key that yields text wins: an array maps
    element-per-bullet (skipping non-scalars); a lone string becomes a single bullet.
    """
    for key in _EVIDENCE_KEYS:
        value = raw.get(key)
        if isinstance(value, list):
            bullets = [
                str(item).strip()
                for item in value
                if isinstance(item, str | int | float) and str(item).strip()
            ]
            if bullets:
                return tuple(bullets)
        elif isinstance(value, str) and value.strip():
            return (value.strip(),)
    return ()


def _parse_suggestion(raw: Any) -> CodeBlock | None:
    """Parse a suggestion — string → {text, code}; strict {language, code}."""
    if isinstance(raw, str):
        return CodeBlock(language="text", code=raw) if raw.strip() else None
    if not isinstance(raw, dict):
        return None
    lang = raw.get("language")
    code = raw.get("code")
    if not isinstance(lang, str) or not lang.strip():
        return None
    if not isinstance(code, str) or not code.strip():
        return None
    return CodeBlock(language=lang, code=code)


# The canonical ``trace`` is the ordered execution-path / chain that grounds a
# claim. New (post-convergence) agents emit ``trace``; the remaining keys normalize
# the pre-convergence chain names so publish still grounds legacy artifacts. Kept
# separate from ``_EVIDENCE_KEYS`` (unordered prose) so a step-chain never renders as
# flat bullets — and vice versa.
_TRACE_KEYS = ("trace", "stepsToReproduce", "exploit_chain", "attack_vector")


def _parse_steps(value: Any) -> tuple[str, ...]:
    """Ordered chain steps: an array maps element-per-step; a lone string → one step."""
    if isinstance(value, list):
        steps = [
            str(item).strip()
            for item in value
            if isinstance(item, str | int | float) and str(item).strip()
        ]
        return tuple(steps)
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _parse_trace(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Capture the ordered grounding chain from the first non-empty trace-family key."""
    for key in _TRACE_KEYS:
        steps = _parse_steps(raw.get(key))
        if steps:
            return steps
    return ()


def _parse_fix(raw: Any) -> CodeBlock | str | None:
    """Parse the canonical ``fix`` union: a ``{language, code}`` block → :class:`CodeBlock`;
    a non-empty prose instruction → ``str``; otherwise ``None``."""
    if isinstance(raw, dict):
        return _parse_suggestion(raw)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


_REMEDIATION_KINDS = ("suggestion", "fix", "draft")


def _fence_language(raw: Any) -> str | None:
    """Reduce a fence-language tag to its first token — a multi-word or multi-line
    value would break the fence it is interpolated into."""
    if not isinstance(raw, str):
        return None
    tokens = raw.strip().split()
    return tokens[0] if tokens else None


def _parse_remediation(
    raw: Any, locations: tuple[NormalizedLocation, ...] | None
) -> Remediation | None:
    """Parse the Buddies draft or legacy typed remediation union.

    Code text is preserved verbatim because indentation matters. A malformed value
    yields ``None``; the owning bundle's shape gate reports the violation.
    """
    if not isinstance(raw, dict):
        return None
    raw_rationale = raw.get("rationale")
    if raw_rationale is None:
        rationale = None
    elif isinstance(raw_rationale, str) and raw_rationale.strip():
        rationale = raw_rationale.strip()
    else:
        return None
    checks = _parse_remediation_checks(raw)
    if "checks" in raw and checks is None:
        return None
    limitations = _parse_optional_nonempty_strings(raw, "limitations")
    if "limitations" in raw and limitations is None:
        return None
    if "kind" not in raw:
        proposal = raw.get("proposal")
        illustration = raw.get("illustration")
        if not isinstance(proposal, str) or not proposal.strip():
            return None
        if not isinstance(illustration, str) or not illustration.strip():
            return None
        return Remediation(
            rationale=rationale,
            proposal=proposal.strip(),
            illustration=illustration,
            language=_fence_language(raw.get("language")),
            checks=checks,
            limitations=limitations or (),
        )
    kind = raw.get("kind")
    if kind == "suggestion":
        replacement = raw.get("replacement")
        if not isinstance(replacement, str) or not replacement.strip():
            return None
        idx = raw.get("anchorIndex")
        target = (
            locations[idx]
            if isinstance(idx, int)
            and not isinstance(idx, bool)
            and locations
            and 0 <= idx < len(locations)
            else None
        )
        return Remediation(
            kind="suggestion",
            rationale=rationale,
            replacement=replacement,
            target=target,
            checks=checks,
            limitations=limitations or (),
        )
    if kind == "fix":
        prose = raw.get("prose")
        if not isinstance(prose, str) or not prose.strip():
            return None
        return Remediation(
            kind="fix",
            rationale=rationale,
            prose=prose.strip(),
            checks=checks,
            limitations=limitations or (),
        )
    if kind == "draft":
        code = raw.get("code")
        if not isinstance(code, str) or not code.strip():
            return None
        return Remediation(
            kind="draft",
            rationale=rationale,
            code=code,
            language=_fence_language(raw.get("language")),
            checks=checks,
            limitations=limitations or (),
        )
    return None


def _parse_remediation_checks(raw: Mapping[str, Any]) -> tuple[RemediationCheck, ...] | None:
    """Parse optional code-reading facts without treating them as execution evidence."""
    value = raw.get("checks")
    if not isinstance(value, list) or not value:
        return None
    parsed = tuple(_parse_remediation_check(entry) for entry in value)
    if any(item is None for item in parsed):
        return None
    return tuple(item for item in parsed if item)


def _parse_remediation_check(raw: Any) -> RemediationCheck | None:
    if not isinstance(raw, Mapping):
        return None
    file_path = raw.get("filePath")
    start_line = raw.get("startLine")
    end_line = raw.get("endLine")
    observation = raw.get("observation")
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
        return None
    if isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < start_line:
        return None
    if not isinstance(observation, str) or not observation.strip():
        return None
    return RemediationCheck(
        NormalizedLocation(file_path.strip(), start_line, end_line), observation.strip()
    )


def _parse_nonempty_strings(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, list):
        return None
    parsed = tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
    return parsed if len(parsed) == len(raw) else None


def _parse_optional_nonempty_strings(raw: Mapping[str, Any], field: str) -> tuple[str, ...] | None:
    return _parse_nonempty_strings(raw.get(field)) if field in raw else None


def _parse_exploitability(raw: Any) -> Exploitability | None:
    """Parse the ``exploitability`` qualifier ``{rating, reasoning}`` (rating required)."""
    if not isinstance(raw, dict):
        return None
    rating = raw.get("rating")
    if not isinstance(rating, str) or not rating.strip():
        return None
    reasoning = raw.get("reasoning")
    reasoning = reasoning.strip() if isinstance(reasoning, str) and reasoning.strip() else None
    return Exploitability(rating=rating.strip(), reasoning=reasoning)


def _parse_locations(
    raw: Any,
    file_key: str = "filePath",
    start_key: str = "startLine",
    end_key: str = "endLine",
) -> tuple[NormalizedLocation, ...] | None:
    """Parse a finding's evidence array into canonical ``NormalizedLocation`` rows.

    The row sub-keys default to ``filePath``/``startLine``/``endLine`` but are
    overridable by the caller's adapter, so a schema may spell them to match its
    native contract without the extractor hard-coding one shape."""
    if not isinstance(raw, list) or not raw:
        return None
    out: list[NormalizedLocation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        fp = entry.get(file_key)
        fp = fp if isinstance(fp, str) else None
        sl_raw = entry.get(start_key)
        sl = int(sl_raw) if is_js_number(sl_raw) and float(sl_raw).is_integer() else None
        el_raw = entry.get(end_key)
        el = int(el_raw) if is_js_number(el_raw) and float(el_raw).is_integer() else None
        if fp or sl is not None:
            out.append(
                NormalizedLocation(
                    file_path=fp, start_line=sl, end_line=el if el is not None else sl
                )
            )
    return tuple(out) if out else None


# ─────────────────────────────────────────────────────────────────────────────
# Collection + normalization
# ─────────────────────────────────────────────────────────────────────────────


def _keep(entry: Any, adapter: _Adapter) -> bool:
    """Row filter: keep when the filter field is truthy and not excluded (no filter → keep)."""
    if adapter.filter_field is None:
        return True
    if not isinstance(entry, dict):
        return False
    value = entry.get(adapter.filter_field)
    return bool(value) and value not in adapter.filter_exclude


def _collect_raws(
    obj: Mapping[str, Any] | None, arrays: Mapping[str, _Adapter]
) -> list[tuple[Mapping[str, Any], _Adapter]]:
    """Gather (row, adapter) pairs for every declared finding array present on ``obj``."""
    raws: list[tuple[Mapping[str, Any], _Adapter]] = []
    if obj is None:
        return raws
    for key, adapter in arrays.items():
        val = obj.get(key)
        if isinstance(val, list):
            raws.extend(
                (entry, adapter)
                for entry in val
                if isinstance(entry, dict) and _keep(entry, adapter)
            )
    return raws


def _severity(raw: Mapping[str, Any], adapter: _Adapter) -> str:
    value = _get(raw, adapter.severity_key)
    if not _present(value) or (isinstance(value, str) and not value.strip()):
        return adapter.default_severity
    rendered = js_str(value)
    return adapter.severity_map.get(rendered, rendered)


def _build_item(agent_name: str, raw: Mapping[str, Any], adapter: _Adapter) -> FindingItem:
    """Normalize a raw finding row into a canonical :class:`FindingItem` via its adapter."""
    locations = _parse_locations(
        _get(raw, adapter.locations_key),
        adapter.location_file_key,
        adapter.location_start_key,
        adapter.location_end_key,
    )
    first = locations[0] if locations else None
    file = first.file_path if first else None
    line = first.start_line if first else None

    description = _str(raw, adapter.description_key, "")
    explicit_id = _get(raw, adapter.id_key)
    if _present(explicit_id) and len(js_str(explicit_id)) > 0:
        fid = js_str(explicit_id)
    else:
        fid = synthesize_stable_id(agent_name, file, line, description)

    line_end_raw = _get(raw, "lineEnd")
    line_end = int(line_end_raw) if is_js_number(line_end_raw) else None

    return FindingItem(
        agent_name=agent_name,
        id=fid,
        severity=_severity(raw, adapter),
        category=_str(raw, adapter.category_key, adapter.default_category),
        judge_category=_non_empty_string(raw, "judge_category"),
        file=file if isinstance(file, str) else None,
        line=line,
        description=description,
        title=_non_empty_string(raw, adapter.title_key),
        line_end=line_end,
        locations=locations,
        evidence=_parse_evidence(raw),
        fix=(
            _parse_fix(_get(raw, "fix"))
            or _parse_suggestion(_get(raw, "suggestion"))
            or _non_empty_string(raw, "recommendation")
        ),
        remediation=_parse_remediation(_get(raw, "remediation"), locations),
        trace=_parse_trace(raw),
        impact=_non_empty_string(raw, "impact"),
        exploitability=_parse_exploitability(_get(raw, "exploitability")),
    )


def extract_findings(
    agent_name: str,
    response: str,
    configuration: Configuration | None = None,
) -> list[FindingItem]:
    """Parse ``response`` and return its findings."""
    try:
        import json as _json

        data = _json.loads(extract_json(response))
    except (ValueError, TypeError):
        return []

    obj = data if isinstance(data, dict) else None
    from roundtable.graph import get_configuration

    config = configuration or get_configuration()
    root = config.root
    arrays = _finding_arrays_by_agent(root).get(agent_name) or _finding_arrays(root)
    raws = _collect_raws(obj, arrays)
    return [_build_item(agent_name, raw, adapter) for raw, adapter in raws]

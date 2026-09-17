"""publish: extract the deterministic ADO publish plan from a review session.

Extracts the deterministic ADO publish plan, plus the MANDATORY-shape telemetry
and the watermark helpers it depends on.

Judge emits a ``verdict_overlay[]`` of ``(source_agent, finding_id)`` references
plus verdict-level decisions (severity override, blocking flag, merge intent).
This module resolves every overlay tuple against the
:class:`SpecialistFindingIndex` and reconstructs a :class:`PublishableFinding`
from the SPECIALIST's evidence (locations, suggestion, …) combined with Judge's
decisions. The output is the input to the ADO REST submission layer.

The **watermark stable hash** is the byte-stability-critical surface:

    sortedLocSig = sorted(f"{filePath or ''}:{startLine or ''}:{endLine or ''}"
                          for loc in allLocations)
    stableHash   = sha1("|".join(str(p).strip().lower()
                                 for p in [id, severity, title, *sortedLocSig])
                        ).hexdigest()[:12]

If this hash algorithm changes, a re-publish creates duplicate PR threads, so
it is locked down by the publish unit tests.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from roundtable.ado import (
    GroundingUnit,
    PublishableFinding,
    compute_stable_hash,
    locations_of,
)
from roundtable.decision import extract_session_id
from roundtable.delivery import PublishableResult
from roundtable.extraction import (
    CodeBlock,
    NormalizedLocation,
)
from roundtable.result_access import response_of
from roundtable.types import severity_rank_map

from .specialist_finding_index import (
    SpecialistFindingIndex,
    SpecialistFindingRecord,
    build_index_key,
    build_specialist_finding_index,
)
from .verdict import parse_judge_summary

# ─── Security classification (SECURITY_PREFIXES) ──
SECURITY_AGENTS = frozenset(
    {"AttackSurfaceScanner", "PenTest", "ExploitEngineer", "SecurityIntentProfiler", "Security"}
)
SECURITY_PREFIXES = ("ASS-", "PT-", "EE-", "SIP-")

# ─── Prose-in-code detection ────────────────
CODE_FILE_EXTENSIONS = frozenset(
    {
        ".cs",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".py",
        ".cpp",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".scala",
        ".sql",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".css",
        ".scss",
    }
)
_IMPERATIVE_VERB_RX = re.compile(
    r"^(remove|add|update|replace|reword|rename|move|delete|use|set|change|"
    r"correct|refactor|inline|extract|consolidate|drop)\b",
    re.IGNORECASE,
)
_CODE_TOKEN_RX = re.compile(r"[{};=<>:()]|=>")
_PROSE_FILE_EXTENSIONS = (".md", ".txt", ".rst", ".markdown")


# ─── Data model ────────────────────────────────────────────────────────────────
@dataclass
class OverlayRef:
    """A resolved ``(source_agent, finding_id)`` reference into the index."""

    source_agent: str
    finding_id: str
    reason: str | None = None
    title: str | None = None


@dataclass
class UnresolvedOverlayRef:
    """An overlay/bucket reference that did NOT resolve against the index."""

    bucket: str  # 'verdict_overlay' | 'merged_with' | 'validated_safe' | 'needs_human_judgment'
    source_agent: str
    finding_id: str


@dataclass
class JudgeObservation:
    """A ``judge_observations[]`` entry (parity surface: id/severity/category/blocking)."""

    id: str
    severity: str
    category: str
    summary: str
    blocking: bool
    related_refs: tuple[OverlayRef, ...] = ()


@dataclass
class PublishPlanDiagnostics:
    """Telemetry accumulated during extraction (parity surface: the 4 counters)."""

    prose_blocks_stripped: int = 0
    suggestions_synthesized: int = 0
    synthesis_failed: int = 0


@dataclass
class PublishPlan:
    """The deterministic publish plan extracted from a session's Judge output."""

    verdict: str
    verdict_icon: str
    session_id: str
    safe_count: int
    original_findings_count: int
    blocking_findings: list[PublishableFinding]
    non_blocking_findings: list[PublishableFinding]
    all_findings: list[PublishableFinding]
    security_findings: list[PublishableFinding]
    published_primary_count: int
    judge_observations: list[JudgeObservation]
    validated_safe_refs: list[OverlayRef]
    needs_human_judgment_refs: list[OverlayRef]
    unresolved_refs: list[UnresolvedOverlayRef]
    diagnostics: PublishPlanDiagnostics


# ─── Helpers ───────────────────────────────────────────────────────────────────
def _read_string(obj: Mapping[str, Any], *keys: str) -> str | None:
    """Return the first non-empty trimmed string value among ``keys``."""
    for key in keys:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def normalize_severity(raw: Any) -> str:
    """Canonical Title-case severity; default Info."""
    s = (str(raw) if raw is not None else "info").lower().strip()
    return {
        "critical": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
    }.get(s, "Info")


def max_severity(severities: Sequence[str | None]) -> str:
    """Highest canonical severity across the merged set (default Low)."""
    best_rank = -1
    best = "Low"
    for s in severities:
        if not s:
            continue
        normalized = normalize_severity(s)
        from .configuration import inspectorx_configuration

        rank = severity_rank_map(inspectorx_configuration())[normalized.lower()]
        if rank > best_rank:
            best_rank = rank
            best = normalized
    return best


def _loc_sig(loc: NormalizedLocation) -> str:
    fp = loc.file_path if loc.file_path is not None else ""
    sl = loc.start_line if loc.start_line is not None else ""
    el = loc.end_line if loc.end_line is not None else ""
    return f"{fp}:{sl}:{el}"


def dedup_locations(locs: Sequence[NormalizedLocation]) -> list[NormalizedLocation]:
    """Dedupe by (file_path, start_line, end_line), preserving order."""
    seen: set[str] = set()
    out: list[NormalizedLocation] = []
    for loc in locs:
        key = _loc_sig(loc)
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
    return out


def detect_prose_in_code_block(
    code: str | None, language: str | None, file_path: str | None
) -> str | None:
    """Return a signal string when the code block is prose, else ``None``."""
    if not isinstance(code, str):
        return None
    body = code.strip()
    if not body:
        return None

    # E.1 — plaintext language on a code-file extension.
    if (
        isinstance(language, str)
        and language.strip().lower() == "plaintext"
        and isinstance(file_path, str)
    ):
        lower = file_path.lower()
        dot = lower.rfind(".")
        if dot >= 0 and lower[dot:] in CODE_FILE_EXTENSIONS:
            return "plaintext_on_code_file"

    # E.2 — heuristic single-line imperative prose (skipped on prose-file extensions).
    if isinstance(file_path, str):
        lower = file_path.lower()
        dot = lower.rfind(".")
        if dot >= 0 and lower[dot:] in _PROSE_FILE_EXTENSIONS:
            return None
    if len(body) > 200:
        return None
    if "\n" in body:
        return None
    if body[-1] not in (".", "?", "!"):
        return None
    if not _IMPERATIVE_VERB_RX.search(body):
        return None
    if _CODE_TOKEN_RX.search(body[:-1]):
        return None
    return "heuristic_prose_imperative"


def _resolve_overlay_finding(
    overlay: Mapping[str, Any],
    blocking: bool,
    index: SpecialistFindingIndex,
    diagnostics: PublishPlanDiagnostics,
    unresolved_refs: list[UnresolvedOverlayRef],
) -> PublishableFinding | None:
    """Reconstruct a ``PublishableFinding`` from an overlay tuple."""
    source_agent = _read_string(overlay, "source_agent")
    finding_id = _read_string(overlay, "finding_id")
    if not source_agent or not finding_id:
        return None

    specialist = index.by_key.get(build_index_key(source_agent, finding_id))
    if specialist is None:
        unresolved_refs.append(UnresolvedOverlayRef("verdict_overlay", source_agent, finding_id))
        print(
            f"[Roundtable][ext] verdict_overlay entry references unknown specialist "
            f"({source_agent}, {finding_id}); skipping",
            file=sys.stderr,
        )
        return None

    # Resolve merged_with subordinates (best-effort).
    merged_with = overlay.get("merged_with")
    merged_with = merged_with if isinstance(merged_with, list) else []
    siblings: list[SpecialistFindingRecord] = []
    for ref in merged_with:
        if not isinstance(ref, Mapping):
            continue
        ref_agent = _read_string(ref, "source_agent")
        ref_id = _read_string(ref, "finding_id")
        if not ref_agent or not ref_id:
            continue
        sib = index.by_key.get(build_index_key(ref_agent, ref_id))
        if sib is None:
            unresolved_refs.append(UnresolvedOverlayRef("merged_with", ref_agent, ref_id))
            print(
                f"[Roundtable][ext] merged_with ref under {specialist.finding_id} references "
                f"unknown specialist ({ref_agent}, {ref_id}); skipping merge",
                file=sys.stderr,
            )
            continue
        siblings.append(sib)

    # Effective severity: explicit verdict_severity override, else max across primary+siblings.
    overlay_severity_raw = _read_string(overlay, "verdict_severity")
    severity_overridden = overlay_severity_raw is not None and len(overlay_severity_raw) > 0
    if severity_overridden:
        effective_severity = normalize_severity(overlay_severity_raw)
    else:
        effective_severity = max_severity(
            [specialist.finding.severity, *(s.finding.severity for s in siblings)]
        )

    # Effective category: explicit override → judge_category → category.
    overlay_category = _read_string(overlay, "category_override")
    effective_category = (
        overlay_category or specialist.finding.judge_category or specialist.finding.category
    )

    # Union locations across primary + siblings (deduped).
    primary_locs = locations_of(specialist.finding)
    sibling_locs: list[NormalizedLocation] = []
    for s in siblings:
        sibling_locs.extend(locations_of(s.finding))
    all_locations = dedup_locations([*primary_locs, *sibling_locs])

    # Source agents: specialist + every merged sibling (insertion-ordered dedupe).
    source_agent_set: dict[str, None] = {}
    source_agent_set[specialist.source_agent] = None
    for s in siblings:
        source_agent_set[s.source_agent] = None
    source_agents = tuple(source_agent_set.keys())

    # Never fall back to the description: it's a paragraph, so promoting it to a
    # one-line title overflows the headline and makes title == description. Leaving
    # it empty lets the renderer headline the finding id and keep the description.
    title = specialist.finding.title or ""

    # Union evidence bullets across primary + merged siblings (order-preserving dedupe).
    evidence_seen: dict[str, None] = {}
    for rec in (specialist, *siblings):
        for bullet in rec.finding.evidence:
            evidence_seen.setdefault(bullet, None)
    evidence = tuple(evidence_seen.keys())

    # Canonical fix: the primary's, else the first merged sibling that carries one.
    fix = specialist.finding.fix
    if fix is None:
        for s in siblings:
            if s.finding.fix is not None:
                fix = s.finding.fix
                break

    # TRAP-E: a one-click `fix` CodeBlock whose body is actually prose is demoted to a
    # plain prose fix, so we never render prose inside a code fence / suggestion.
    if isinstance(fix, CodeBlock):
        probe_file = all_locations[0].file_path if all_locations else None
        if detect_prose_in_code_block(fix.code, fix.language, probe_file) is not None:
            diagnostics.prose_blocks_stripped += 1
            fix = fix.code

    # Canonical remediation (typed union: suggestion/fix/draft). Same
    # primary-then-sibling resolution as ``fix``.
    remediation = specialist.finding.remediation
    if remediation is None:
        for s in siblings:
            if s.finding.remediation is not None:
                remediation = s.finding.remediation
                break

    # Grounding: keep each agent's ordered trace/impact/exploitability as a WHOLE
    # provenance-tagged unit; dedupe identical units, never merge steps (E4).
    grounding_units: list[GroundingUnit] = []
    seen_units: set[tuple[Any, ...]] = set()
    for rec in (specialist, *siblings):
        f = rec.finding
        if not (f.trace or f.impact or f.exploitability):
            continue
        unit = GroundingUnit(
            source_agent=rec.source_agent,
            trace=f.trace,
            impact=f.impact,
            exploitability=f.exploitability,
        )
        key = (unit.source_agent, unit.trace, unit.impact, unit.exploitability)
        if key in seen_units:
            continue
        seen_units.add(key)
        grounding_units.append(unit)
    grounding = tuple(grounding_units)

    # Watermark stable hash.
    sorted_loc_sig = sorted(_loc_sig(loc) for loc in all_locations)
    stable_hash = compute_stable_hash(
        [specialist.finding_id, effective_severity, title, *sorted_loc_sig]
    )

    # A `suggestion` remediation must attach its one-click `` ```suggestion `` block to the
    # exact anchor the replacement targets, not merely the first (deduped/reordered)
    # location. The target span was resolved at extraction and is one of the finding's
    # own locations, so promoting it to primary is merge-safe. Reorder so the target
    # leads; primary/additional then derive cleanly.
    if (
        remediation is not None
        and remediation.kind == "suggestion"
        and remediation.target is not None
        and all_locations
    ):
        tgt_sig = _loc_sig(remediation.target)
        reordered = [loc for loc in all_locations if _loc_sig(loc) == tgt_sig]
        reordered += [loc for loc in all_locations if _loc_sig(loc) != tgt_sig]
        all_locations = reordered

    primary_loc = all_locations[0] if all_locations else None
    additional = tuple(all_locations[1:]) if len(all_locations) > 1 else ()

    return PublishableFinding(
        id=specialist.finding_id,
        title=title,
        # Never backfill the description from the title: an empty description means
        # "the agent gave no separate explanation", which the renderer treats as
        # "omit the Issue section" rather than repeating the headline verbatim.
        description=specialist.finding.description or "",
        severity=effective_severity,
        file_path=primary_loc.file_path if primary_loc else None,
        start_line=primary_loc.start_line if primary_loc else None,
        end_line=primary_loc.end_line if primary_loc else None,
        location_index=0,
        total_locations=len(all_locations),
        additional_locations=additional,
        stable_hash=stable_hash,
        category="blocking" if blocking else "non_blocking",
        judge_category=effective_category,
        source_agents=source_agents,
        evidence=evidence,
        fix=fix,
        remediation=remediation,
        grounding=grounding,
    )


def _parse_overlay_refs(
    bucket: Any,
    bucket_name: str,
    index: SpecialistFindingIndex,
    unresolved_refs: list[UnresolvedOverlayRef],
) -> list[OverlayRef]:
    """Resolve a ``{source_agent, finding_id, reason?}`` bucket into refs."""
    if not isinstance(bucket, list):
        return []
    out: list[OverlayRef] = []
    for raw in bucket:
        if not isinstance(raw, Mapping):
            continue
        source_agent = _read_string(raw, "source_agent")
        finding_id = _read_string(raw, "finding_id")
        if not source_agent or not finding_id:
            continue
        if build_index_key(source_agent, finding_id) not in index.by_key:
            unresolved_refs.append(UnresolvedOverlayRef(bucket_name, source_agent, finding_id))
            print(
                f"[Roundtable][ext] {bucket_name} entry references unknown specialist "
                f"({source_agent}, {finding_id}); ignoring",
                file=sys.stderr,
            )
            continue
        reason = _read_string(raw, "reason")
        resolved = index.by_key.get(build_index_key(source_agent, finding_id))
        out.append(
            OverlayRef(
                source_agent=source_agent,
                finding_id=finding_id,
                reason=reason,
                title=resolved.finding.title if resolved else None,
            )
        )
    return out


def _parse_judge_observations(bucket: Any) -> list[JudgeObservation]:
    """Parse typed ``judge_observations[]`` records."""
    if not isinstance(bucket, list):
        return []
    out: list[JudgeObservation] = []
    for raw in bucket:
        if not isinstance(raw, Mapping):
            continue
        obs_id = _read_string(raw, "id")
        summary = _read_string(raw, "summary")
        if not obs_id or not summary:
            continue
        severity = normalize_severity(raw.get("severity"))
        category = _read_string(raw, "category") or "General"
        blocking = raw.get("blocking") is True
        related_raw = raw.get("related_refs")
        related_raw = related_raw if isinstance(related_raw, list) else []
        related: list[OverlayRef] = []
        for ref in related_raw:
            if not isinstance(ref, Mapping):
                continue
            sa = _read_string(ref, "source_agent")
            fid = _read_string(ref, "finding_id")
            if sa and fid:
                related.append(OverlayRef(source_agent=sa, finding_id=fid))
        out.append(
            JudgeObservation(
                id=obs_id,
                severity=severity,
                category=category,
                summary=summary,
                blocking=blocking,
                related_refs=tuple(related),
            )
        )
    return out


def _is_security_finding(f: PublishableFinding) -> bool:
    """TRAP-C UNION: source_agent ∈ SECURITY_AGENTS, or id prefix, or judge_category='security'."""
    if any(agent in SECURITY_AGENTS for agent in f.source_agents):
        return True
    if any(f.id.startswith(prefix) for prefix in SECURITY_PREFIXES):
        return True
    jc = f.judge_category
    return isinstance(jc, str) and jc.strip().lower() == "security"


def extract_publish_plan(
    session_results: Mapping[str, Any],
    session_dir_path: str | None = None,
) -> PublishPlan | None:
    """Extract the publish plan from a session's per-agent results.

    Returns ``None`` for the empty-plan paths (no/empty/unparseable Judge),
    mirroring the verdict layer's UNKNOWN result — there is nothing to publish.
    """
    judge_response = response_of(session_results.get("Judge"))
    if judge_response is None:
        return None

    from .configuration import inspectorx_configuration

    summary = parse_judge_summary(judge_response, inspectorx_configuration())
    if not summary.parsed or not isinstance(summary.raw_json, dict):
        return None

    json_obj: dict[str, Any] = summary.raw_json
    diagnostics = PublishPlanDiagnostics()

    index = build_specialist_finding_index(session_results)
    unresolved_refs: list[UnresolvedOverlayRef] = []

    overlay_raw_list = json_obj.get("verdict_overlay")
    overlay_raw_list = overlay_raw_list if isinstance(overlay_raw_list, list) else []

    blocking_findings: list[PublishableFinding] = []
    non_blocking_findings: list[PublishableFinding] = []
    for overlay in overlay_raw_list:
        if not isinstance(overlay, Mapping):
            continue
        blocking = overlay.get("blocking") is True
        finding = _resolve_overlay_finding(overlay, blocking, index, diagnostics, unresolved_refs)
        if finding is None:
            continue
        (blocking_findings if blocking else non_blocking_findings).append(finding)

    all_findings = [*blocking_findings, *non_blocking_findings]

    validated_safe_refs = _parse_overlay_refs(
        json_obj.get("validated_safe"), "validated_safe", index, unresolved_refs
    )
    needs_human_judgment_refs = _parse_overlay_refs(
        json_obj.get("needs_human_judgment"), "needs_human_judgment", index, unresolved_refs
    )
    judge_observations = _parse_judge_observations(json_obj.get("judge_observations"))

    security_findings = [f for f in all_findings if _is_security_finding(f)]

    return PublishPlan(
        verdict=summary.verdict,
        verdict_icon=summary.verdict_icon,
        session_id=extract_session_id(session_dir_path),
        safe_count=len(validated_safe_refs),
        original_findings_count=len(overlay_raw_list),
        blocking_findings=blocking_findings,
        non_blocking_findings=non_blocking_findings,
        all_findings=all_findings,
        security_findings=security_findings,
        published_primary_count=len(overlay_raw_list),
        judge_observations=judge_observations,
        validated_safe_refs=validated_safe_refs,
        needs_human_judgment_refs=needs_human_judgment_refs,
        unresolved_refs=unresolved_refs,
        diagnostics=diagnostics,
    )


def check_count_parity(plan: PublishPlan) -> str | None:
    """Count-parity ABORT gate.

    Returns a failure reason string when the plan must NOT be published, or
    ``None`` when it is safe. Three invariants, all on UNFILTERED counts:

      1. Exactly one publishable entry per ``verdict_overlay`` primary
         (``merged_with`` siblings consolidate into the primary, not fan out):
         ``len(all_findings) == published_primary_count``.
      2. No unresolved overlay references — a non-empty ``unresolved_refs``
         indicates an upstream validator bypass; publishing partial findings
         would mislead the humans reading ``verdict.md``.
      3. Defensive prompt-drift guard: ``published_primary_count`` must equal
         ``original_findings_count`` when both are positive.
    """
    if len(plan.all_findings) != plan.published_primary_count:
        return (
            f"Count parity violation (extractor bug): allFindings={len(plan.all_findings)} "
            f"!= publishedPrimaryCount={plan.published_primary_count}. The extractor must "
            f"produce exactly one publishable entry per verdict_overlay primary."
        )
    if plan.unresolved_refs:
        head = ", ".join(
            f"({r.bucket} -> {r.source_agent}::{r.finding_id})" for r in plan.unresolved_refs[:5]
        )
        ellipsis = ", ..." if len(plan.unresolved_refs) > 5 else ""
        return (
            f"Overlay resolution failure: {len(plan.unresolved_refs)} reference(s) could not be "
            f"resolved against the SpecialistFindingIndex [{head}{ellipsis}]. Re-run review — "
            f"the producer's configured output-validation gates should reject these at the source."
        )
    if (
        plan.published_primary_count > 0
        and plan.original_findings_count > 0
        and plan.published_primary_count != plan.original_findings_count
    ):
        return (
            f"Judge count mismatch (prompt drift): publishedPrimaryCount="
            f"{plan.published_primary_count} but verdict_overlay has "
            f"{plan.original_findings_count} entry(ies). Re-run review before publishing."
        )
    return None


def format_log_summary(plan: PublishPlan) -> str:
    return (
        f"plan: verdict={plan.verdict} {plan.verdict_icon} | "
        f"blocking={len(plan.blocking_findings)} "
        f"non-blocking={len(plan.non_blocking_findings)} "
        f"security={len(plan.security_findings)} safe={plan.safe_count} | "
        f"observations={len(plan.judge_observations)}"
    )


class VerdictOverlayProjector:
    name = "verdict_overlay"

    def project(
        self, session_results: Mapping[str, Any], *, session_dir_path: str | None = None
    ) -> PublishableResult | None:
        plan = extract_publish_plan(session_results, session_dir_path=session_dir_path)
        if plan is None:
            return None
        return PublishableResult(
            all_findings=plan.all_findings,
            session_id=plan.session_id,
            verdict=plan.verdict,
            counts={
                "blocking": len(plan.blocking_findings),
                "nonBlocking": len(plan.non_blocking_findings),
                "all": len(plan.all_findings),
                "security": len(plan.security_findings),
            },
            abort_reason=check_count_parity(plan),
            log_summary=format_log_summary(plan),
        )

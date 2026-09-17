"""security_packs: ``security_focus_pack`` derivation + intent-pack vocabulary.

Derives the ``security_focus_pack`` and intent-pack vocabulary injected for the
security consumers (``Security``, ``AttackSurfaceScanner``, ``PenTest``,
``ExploitEngineer``). The design encodes four invariants — each one a guard
against a class of bug, so do not "optimise" them away:

* **Single SIP injection (no duplication).** The four consumers hard-dep
  ``SecurityIntentProfiler`` (SIP). Its output is delivered to them EXACTLY ONCE:
  ``context/injection.py`` relabels the SIP hard-dep section to
  ``## security_intent_pack [REQUIRED]`` (matching the prompt vocabulary
  ``security_intent_pack.selected_sec_checks``). Never also re-serialise the same
  SIP JSON under a second heading — that would ship the identical payload twice.

* **No dead relevance heuristic.** The pack is derived DIRECTLY from the static SEC
  catalog + the OWASP map + SIP's ``selected_sec_checks`` + diff keyword evidence.
  Do NOT add a category-relevance scorer: SIP is a core always-run agent the
  consumers hard-dep, so its selection always determines the category verdicts and
  any scorer's output would be unconditionally overwritten (dead code). On RELEVANT
  categories the ``reason`` is the canonical ``"Selected by Security Intent
  Profiler"``. Empty SIP selection ⇒ empty scope (all categories SKIP) — the
  ``all_skip`` decision.

* **``priority_scenarios`` is SIP-only.** An empty SIP ``priority_scenarios`` ⇒ an
  empty list. Do NOT reconstruct scenarios from other inputs (profiler inversion
  scenarios / data-flow map / grep evidence): those inputs are outside this
  module's derivation set.

* **``selected_sec_checks`` is SIP-only.** All-SKIP ⇒ empty.

Deliberately out of scope: injecting ``## deterministic_scan_result`` /
``## targeted_security_grep_signals`` into SIP's OWN context — SIP's prompt
references neither, so omitting them creates no dangling reference.
"""

from __future__ import annotations

import json
import re
from typing import Any

from roundtable.utils import extract_json

# ─── Static catalog: the 17 security categories ─────────────────────────────
# secRange uses an EN DASH (U+2013); it is retained verbatim in the emitted
# ``category_map`` for byte-stable output.
CATEGORIES: tuple[tuple[int, str, str], ...] = (
    (1, "Authentication & Identity", "SEC-001–015"),
    (2, "Authorization & Access Control", "SEC-016–035"),
    (3, "Input Validation & Injection", "SEC-036–060"),
    (4, "Secrets & Key Management", "SEC-061–075"),
    (5, "Cryptography", "SEC-076–090"),
    (6, "Error Handling & Info Disclosure", "SEC-091–110"),
    (7, "Service-to-Service Communication", "SEC-111–125"),
    (8, "Data Protection & Privacy", "SEC-126–145"),
    (9, "File & Resource Handling", "SEC-146–165"),
    (10, "Business Logic", "SEC-166–185"),
    (11, "Dependency & Supply Chain", "SEC-186–200"),
    (12, "Logging & Monitoring", "SEC-201–220"),
    (13, "Configuration & Hardening", "SEC-221–240"),
    (14, "API Security", "SEC-241–260"),
    (15, "Build, Release & CI/CD", "SEC-261–280"),
    (16, "Mobile & Client Security", "SEC-281–300"),
    (17, "Resilience & DoS", "SEC-301–320"),
)

# OWASP mapping keyed by category num.
OWASP_CATEGORY_MAP: dict[int, tuple[tuple[str, str], ...]] = {
    1: (("A07", "Authentication Failures"),),
    2: (("A01", "Broken Access Control"),),
    3: (("A03", "Injection"),),
    5: (("A02", "Cryptographic Failures"),),
    7: (("A10", "Server-Side Request Forgery"),),
    11: (("A06", "Vulnerable and Outdated Components"),),
    12: (("A09", "Security Logging and Monitoring Failures"),),
    13: (("A05", "Security Misconfiguration"), ("A04", "Insecure Design")),
    15: (("A08", "Software and Data Integrity Failures"),),
}

# Keyword set scanned over added diff lines.
SECURITY_SCOPE_KEYWORDS: tuple[str, ...] = (
    "auth",
    "token",
    "secret",
    "crypto",
    "encrypt",
    "decrypt",
    "permission",
    "role",
    "tenant",
    "injection",
    "sql",
    "path",
    "deserialize",
    "serialize",
    "command",
    "exec",
    "ssrf",
    "csrf",
    "xss",
    "headers",
    "jwt",
    "cert",
    "signature",
    "etag",
    "queue",
    "pubsub",
    "ipc",
    "ffi",
)

_RANGE_RE = re.compile(r"^(SEC-\d+)–(\d+)$")  # EN DASH form, e.g. SEC-016–035
_FILE_RE = re.compile(r"^\+\+\+\s+[ab]/([^\t\n\r]+)")
_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

_MAX_GREP_EVIDENCE = 12


def normalize_sec_range(sec_range: str) -> str:
    """Canonicalise a SEC range so catalog (``SEC-016–035``) and SIP
    (``SEC-016-SEC-035``) forms compare equal."""
    m = _RANGE_RE.match(sec_range)
    if not m:
        return sec_range.replace("–", "-")
    return f"{m.group(1)}-SEC-{m.group(2)}"


def extract_security_keyword_evidence(diff: str) -> list[dict[str, Any]]:
    """Scan added diff lines for security keywords (file/line/keyword), capped at
    12. Pure/offline."""
    evidence: list[dict[str, Any]] = []
    current_file = ""
    current_line = 0

    for line in diff.split("\n"):
        fm = _FILE_RE.match(line)
        if fm and fm.group(1) != "/dev/null":
            current_file = fm.group(1)
            current_line = 0
            continue

        hm = _HUNK_RE.match(line)
        if hm:
            current_line = int(hm.group(1))
            continue

        if not current_file or not line.startswith("+") or line.startswith("+++"):
            if current_line > 0 and not line.startswith("-"):
                current_line += 1
            continue

        lower = line.lower()
        for keyword in SECURITY_SCOPE_KEYWORDS:
            if keyword in lower:
                evidence.append(
                    {"file": current_file, "line": current_line or 1, "keyword": keyword}
                )
                break
        current_line += 1
        if len(evidence) >= _MAX_GREP_EVIDENCE:
            break

    return evidence


def _sip_selection(sip_response: str) -> tuple[list[str], list[str]]:
    """Extract ``(selected_sec_checks, priority_scenarios)`` from the raw SIP JSON.

    Defensive: non-string / empty entries dropped; returns empty lists on any
    parse failure or non-object payload.
    """
    try:
        parsed = json.loads(extract_json(sip_response))
    except (ValueError, TypeError):
        return [], []
    if not isinstance(parsed, dict):
        return [], []

    raw_sel = parsed.get("selected_sec_checks")
    selected = [str(v) for v in raw_sel if str(v)] if isinstance(raw_sel, list) else []
    raw_pri = parsed.get("priority_scenarios")
    priority = [str(v) for v in raw_pri if str(v)] if isinstance(raw_pri, list) else []
    return selected, priority


def build_security_focus_pack(sip_response: str, diff: str) -> dict[str, Any]:
    """Derive the ``security_focus_pack`` from SIP output + the diff.

    Derived directly from the static SEC catalog + the OWASP map + SIP selection +
    diff keyword evidence; there is no relevance scorer (its verdicts would be
    overwritten by SIP selection in the only path that runs). Empty SIP selection ⇒
    every category SKIP (the ``all_skip`` decision). Key order is fixed for
    byte-stable serialisation.
    """
    selected, priority = _sip_selection(sip_response)
    selected_sec_checks = list(dict.fromkeys(normalize_sec_range(v) for v in selected))
    selected_lookup = set(selected_sec_checks)

    category_map: list[dict[str, Any]] = []
    for num, category, sec_range in CATEGORIES:
        if not selected_lookup:
            status, reason = "SKIP", "No SEC ranges selected by Security Intent Profiler"
        elif normalize_sec_range(sec_range) in selected_lookup:
            status, reason = "RELEVANT", "Selected by Security Intent Profiler"
        else:
            status, reason = "SKIP", "Filtered out by Security Intent Profiler"
        category_map.append(
            {
                "num": num,
                "category": category,
                "secRange": sec_range,
                "status": status,
                "reason": reason,
            }
        )

    selected_owasp: list[dict[str, str]] = []
    excluded_owasp: list[dict[str, str]] = []
    seen_selected: set[str] = set()
    seen_excluded: set[str] = set()
    for cat in category_map:
        for owasp_id, owasp_name in OWASP_CATEGORY_MAP.get(cat["num"], ()):  # type: ignore[index]
            if cat["status"] == "RELEVANT":
                target, seen = selected_owasp, seen_selected
            else:
                target, seen = excluded_owasp, seen_excluded
            if owasp_id in seen:
                continue
            seen.add(owasp_id)
            target.append({"id": owasp_id, "name": owasp_name, "reason": cat["reason"]})

    return {
        "selected_owasp": selected_owasp,
        "selected_sec_checks": selected_sec_checks,
        "priority_scenarios": list(priority),
        "grep_evidence": extract_security_keyword_evidence(diff),
        "excluded_owasp": excluded_owasp,
        "category_map": category_map,
    }


def format_security_focus_pack_body(pack: dict[str, Any]) -> str:
    """Render the focus pack as a fenced JSON block (no heading): 2-space indent,
    raw unicode, ``": "`` separators (``json.dumps(indent=2, ensure_ascii=False)``).

    The heading is supplied by the generic injection path via the
    ``SecurityFocusPack`` node's declared ``delivery_label`` (``## security_focus_pack``),
    so this returns only the body — keeping the deterministic-enricher contract
    uniform (``fn -> body``, heading-agnostic).
    """
    body = json.dumps(pack, indent=2, ensure_ascii=False)
    return f"```json\n{body}\n```"

"""persistence.trace_keys: the single source of truth for the **neutral**
``trace.json`` key names.

``trace.json`` is deliberately **not** schema-governed (schema-backed agent output
uses the graph-declared schema — see ``AGENTS.md``), so its keys are hand-serialised. Before
this module those key names lived as bare string literals duplicated across the
writer (:mod:`roundtable.persistence.trace`) and its reader
(:mod:`roundtable.review.acceptance`); a typo on either side drifted silently because
the writer omits empty keys (a mis-spelled key just never appears).

Centralising the names here makes a typo a resolvable-name error instead: writer
and reader import the same constant, so they cannot drift. The *values* are the
wire contract — the acceptance tests still assert the literal strings, so a typo in
a constant's value is caught by those tests rather than hidden.

**Scope: neutral run record only.** The review-DOMAIN half of ``trace.json`` (the
verdict surface, finding ``counts``, ``subject``/``diffStat``/``provenance``
blocks) is assembled as an opaque *overlay* whose key names live in
:class:`roundtable.review.trace_overlay.OverlayKey`. This module knows only the
config-agnostic fields the generic ``persistence`` core writes itself.

Grouped by where each key appears; a name shared across scopes (``usage``,
``wallClockMs``) is defined once and reused.
"""

from __future__ import annotations


class TraceKey:
    """Namespace of the neutral ``trace.json`` key names (the wire contract)."""

    # ── top-level (neutral run record) ───────────────────────────────────────
    SESSION_ID = "sessionId"
    AGENT_COUNT = "agentCount"
    STARTED_AT = "startedAt"
    FINISHED_AT = "finishedAt"
    AGENTS = "agents"
    MCP_PREWARM = "mcpPrewarm"

    # ── derived-index row fields (read back by :mod:`output.session_index`) ───
    DURATION_SECONDS = "durationSeconds"
    OUTPUT_TOKENS = "outputTokens"
    BILLING = "billing"
    BILLING_SOURCE = "source"
    BILLING_STATUS = "status"
    TOTAL_NANO_AIU = "totalNanoAiu"
    TOTAL_PREMIUM_REQUEST_COST = "totalPremiumRequestCost"
    BILLING_WARNING_CODE = "warningCode"
    # Legacy machine telemetry read from pre-AI-Credit artifacts only.
    PREMIUM_REQUESTS = "premiumRequests"

    # ── per-agent entry ──────────────────────────────────────────────────────
    AGENT = "agent"
    VALID = "valid"
    GATE = "gate"
    ATTEMPTS = "attempts"
    RESPONSE = "response"
    TOOL_STATS = "toolStats"
    ATTEMPTS_DETAIL = "attemptsDetail"
    WARNINGS = "warnings"
    ERRORS = "errors"
    SUBMISSION_STATUS = "submissionStatus"

    # ── per-attempt (``attemptsDetail[]``) ───────────────────────────────────
    ATTEMPT = "attempt"
    MODEL = "model"
    OUTCOME = "outcome"
    RETRY_FEEDBACK = "retryFeedback"
    BACKEND_OUTCOME = "backendOutcome"
    TOOLS_INVOKED = "toolsInvoked"
    TOOL_ERRORS = "toolErrors"
    EXECUTION_POLICY = "executionPolicy"
    SUBMISSIONS = "submissions"

    # ── shared across scopes ─────────────────────────────────────────────────
    USAGE = "usage"
    WALL_CLOCK_MS = "wallClockMs"

    # ── MCP pre-flight entry (``mcpPrewarm[]``) ──────────────────────────────
    NAME = "name"

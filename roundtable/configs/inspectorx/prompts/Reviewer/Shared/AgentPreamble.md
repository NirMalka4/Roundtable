# Shared Agent Preamble

> **IMPORTANT**: This content is shared across agents that list it in their `shared_context`
> (see `configs/inspectorx/agent_graph.yaml`). Do NOT duplicate it in individual agent files.

---

## ⚠️ ANTI-ATTENTION-DRIFT PROTOCOL (v3)

> **CRITICAL**: All agents MUST follow the Anti-Attention-Drift Protocol.
> See: `Shared/AntiDriftProtocol.md` for full documentation.

### Key Principles

1. **Severity-Blind *Enumeration***: Do NOT let severity gate what you report — enumerate ALL findings first, THEN assign each a **baseline severity**. The terminal SeverityInflator flow *escalates* baselines via multipliers; it does not assign them from scratch. (Non-emitting agents — profilers, simulators, consumers — have no severity field; this applies only to findings-emitting agents.)

2. **Zero-Drop Mandate**: Once you enumerate a finding, it MUST appear in your output — never summarize it away, filter it out "for brevity", or run out of tokens and omit it. **Carve-out**: withholding a candidate that fails your *own* evidence/proof gate is **not** a drop — proof-gated specialists (e.g. SchemaDrift's Proof Envelope, the PenTest/ExploitEngineer exploitability bar) legitimately never emit an unproven candidate; a candidate that never becomes a finding has nothing to drop. *(Judge-side accounting: the Judge buckets every specialist finding it receives into `verdict_overlay[]`, `validated_safe`, or `needs_human_judgment`; that aggregation rule is for the Judge, not for specialists.)*

3. **Category-First Processing**: Group findings by category, not severity. Process ALL findings in each category before moving to the next.

4. **Continuation Mandate**: If interrupted, output what you have with `continuation_needed: true` and list remaining work.

5. **Zero Assumptions Mandate**: Do not assume anything — not file locations, not project structure, not runtime behavior, not workspace completeness. Every conclusion MUST be validated against the actual source of truth (filesystem, source code, API response). Cached or stale context (workspace snapshots, session metadata) is not reality — the filesystem and git are reality. If you cannot verify a claim, state it as unverified. When in doubt, **look**.

---

## Repository Access

You have **full read access to the entire repository**. Use this to:
- **Navigate to referenced files**: Open files mentioned in the diff to understand the full context.
- **Trace entry points**: Follow public APIs, controllers, and event handlers to their implementations.
- **Examine dependencies**: Read dependent files to understand the impact radius of changes.
- **Check configuration**: Review config files, DI registrations, and startup code.
- **Find related code**: Search for similar patterns or related functionality.

**Do not guess context, do not assume anything** - always verify by reading the source files.

**CRITICAL**: Do NOT call `git rev-parse --abbrev-ref HEAD` during a worktree review — it returns `"HEAD"` in a detached worktree. Always use `source_branch` from the **## Change Under Review** (set from the `--source-branch` argument).

---

## Output Contract

All agents MUST structure their output according to their own Output Schema, which is
provided directly in your context (your schema-contract slice, or your agent body). Do not
assume the full multi-agent schema catalog is available in context.
Malformed outputs will cause handoff failures between agents.

### Canonical Finding Location (single shape — ALL finding-bearing agents)

Every code location on a **finding** MUST be expressed as a single canonical array:

```yaml
locations:
  - filePath: src/payments/Refund.cs   # repo-relative path
    startLine: 142
    endLine: 167                        # == startLine for a point finding
```

- **One shape, everywhere.** Do NOT emit the legacy singular `file`/`line` fields, and do
  NOT emit a bare `location` string. The validator hard-rejects those at the schema gate
  (`roundtable/validation/ovg.py` → `_validate_locations_array`); downstream extraction and
  ADO publishing read `locations[]` **only**.
- **Ranges are real.** Use `startLine..endLine` to span the whole construct (method, class,
  catch block, duplicated region). Collapse to `startLine == endLine` ONLY for a genuine
  point finding (a magic number, a single declaration).
- **Scope.** This governs the `findings[]` location channel. It does NOT apply to non-finding
  coordinate fields some agents legitimately emit (e.g. SecurityIntentProfiler
  `security_cracks[].evidence[].file/line` pointers, DeterministicPreScan `flagged[].file/line`)
  — those keep their own documented shapes. Non-emitting agents (profilers, simulators,
  consumers) emit no findings and therefore no `locations[]`.

### High-Level Output Shell (Readability Convention)

When producing human-readable sections, use this top-level order so outputs are easy to scan.
This shell is additive for readability and NEVER replaces or removes your schema's required fields.
Apply only the sections that fit your agent's role: emit your schema's PRIMARY output type in place
of `Findings` (e.g. profilers emit a map/scenarios, SeverityInflator emits `inflated_findings`, the
Judge emits its verdict). A non-emitting agent simply omits the sections that do not apply.

1. `Summary` — verdict/outcome, scope, and standards/inputs considered. (Always.)
2. `Checks/Dimensions` — a compact table or list of what was evaluated. (When applicable.)
3. `Findings` (or your schema's primary output) — structured records using YOUR agent schema. (Only if your schema emits them.)
4. `Remediation/Recommendations` — actionable fixes or next actions. (When applicable.)
5. `Evidence/Notes` — traceability details, caveats, and residual risk. (When applicable.)

---

## ⚠️ TEMPLATE COMPLIANCE (ZERO DRIFT)

> **CRITICAL**: When a template is provided, it is a **SCHEMA**, not guidance.

### Template = Fill-in-the-Blanks

```yaml
template_rules:
  1_exact_structure:
    - Output EXACTLY the sections in template, in EXACT order
    - Do NOT add sections not in template
    - Do NOT remove sections (use "None" if empty)
    - Do NOT rename headers
    
  2_no_improvisation:
    - Do NOT add explanatory paragraphs not in template
    - Do NOT add code blocks where template has none
    - Do NOT add subsections (Impact, Timeline, Scenario, etc.)
    - Do NOT add ASCII diagrams or flowcharts
    
  3_placeholder_semantics:
    - "{{variable}}" = Replace with value, keep surrounding text
    - "{{#each items}}" = Repeat block per item, nothing more
    - NEVER add content outside placeholders
    
  4_code_placement:
    - Code blocks go ONLY where template shows code blocks
    - In verdict: ALL code goes in "💡 Suggested Fixes", NEVER in findings
```

### Pre-Output Checklist

```
□ Does output have EXACTLY the template sections?
□ Did I add ANY section not in template? → REMOVE IT
□ Did I add content not indicated by {{placeholders}}? → REMOVE IT
□ Is structure 1:1 match with template?
```

**Violation = Drift = Regenerate**

---

## ⚠️ DEPTH-FIRST ANALYSIS PROTOCOL (CRITICAL)

> **Principle**: A finding that hasn't been verified from source code is not a finding - it's a guess.

### 1. VERIFY BEFORE REPORTING

**NEVER report a finding without:**
- Reading the actual code at the location (not just the diff snippet)
- Reading at least 50 lines of surrounding context
- Tracing data flow one level up AND one level down
- Confirming the issue exists in the CURRENT code (not just the diff)

**Anti-Pattern Checklist** (DO NOT report these without verification):
| Pattern You See | MUST Verify Before Reporting |
|-----------------|------------------------------|
| "Potential null reference" | Read caller code - is it actually nullable? |
| "Thread safety issue" | Trace the collection - is it actually shared? |
| "Missing null check" | Check if null is possible at this call site |
| "Breaking change" | Verify consumers exist and would break |
| "Performance issue" | Confirm it's in a hot path, not one-time init |

### 2. TRACE THE DATA FLOW

For every finding, you MUST be able to answer:
1. **Where does the data come from?** (Read the caller/source)
2. **Where does the data go?** (Read the consumer/sink)
3. **What transforms happen?** (Read intermediate handlers)

If you cannot trace the flow → **Read more code before reporting**.

### 3. SELF-CHALLENGE WITHIN ANALYSIS

Before finalizing ANY finding, ask yourself:
- "What would prove me WRONG?"
- "Is there a code path that makes this safe?"
- "Did I check the type hierarchy / interface implementation?"

**If you find a counterexample → DROP the finding, don't report it.**

### 4. MINIMUM CONTEXT THRESHOLDS

| Analysis Type | Minimum Context Required |
|---------------|-------------------------|
| Null safety | Read the full method + callers |
| Thread safety | Read all usages of the shared state |
| Breaking change | Read all implementers/consumers |
| Logic error | Trace full execution path |
| Security issue | Read auth/validation chain end-to-end |

### 5. TEST VALIDATION

You operate **read-only** (`view`/`rg`/`glob`) and do **not** execute tests. Validate statically:
- Check whether an existing test already covers the scenario you're concerned about.
- If a passing test plausibly exercises that code path, treat your "bug" as unverified — it may be a misunderstanding; calibrate evidence status down accordingly.
- If no test covers it, record the coverage gap as part of the finding rather than attempting to run anything.

### 6. EVIDENCE STATUS CALIBRATION

Only report findings where evidence is **VERIFIED**:

| Status | Definition | Action |
|--------|------------|--------|
| `VERIFIED` | Full data flow traced, counterexamples checked, no gaps | Report the finding |
| `PARTIAL` | Some evidence found, but gaps remain | Route to adversarial evidence review OR "Needs Human Judgment" |
| `SUSPECTED` | Pattern matches but unverified | Drop or investigate further |

> **Golden Rule**: One verified finding is worth more than ten guesses.

---

## ⚠️ FALSE POSITIVE PREVENTION (MANDATORY CHECK)

Before flagging ANY finding, check `Shared/KnownFalsePositivePatterns.md`:

```
FOR EACH FINDING:
│
├─► Does this match a known false positive pattern?
│   │
│   ├─► YES → Apply the documented mitigation
│   │   │
│   │   └─► Still an issue after mitigation?
│   │       ├─► YES → Flag with evidence showing mitigation doesn't apply
│   │       └─► NO → DROP the finding
│   │
│   └─► NO → Proceed with normal flagging
```

**Common False Positive Categories** (see full list in KnownFalsePositivePatterns.md):
- Null reference without checking caller context
- Thread safety false alarms (different collections)
- SQL injection when query is parameterized
- Exception "swallowed" but actually logged
- Audit mode code not affecting production

---

## Evidence vs Repro Steps (ALL AGENTS)

Not every finding has repro steps. Style issues, code duplication, naming violations — these have **evidence** (the code location, the pattern observed) but no step-by-step flow to reproduce.

However, when a finding describes a **data flow, intent flow, or failure path** that can practically occur at runtime, the evidence MUST be presented as **numbered repro steps** — concrete actions or conditions that lead to the observed outcome. This makes findings actionable and verifiable.

### Decision Rule

```
FOR EACH FINDING:
│
├─► Is this a style, naming, duplication, or static-pattern issue?
│   └─► YES → Use "Evidence" (bullet list or brief description)
│
├─► Does this involve a data flow, control flow, failure path, or attack vector
│   that can practically occur at runtime?
│   └─► YES → Use "Repro Steps" (numbered steps showing HOW it happens)
│
└─► When in doubt: if you can describe a sequence of actions/conditions
    that leads to the outcome → Repro Steps. Otherwise → Evidence.
```

### Repro Steps Formatting Rules

1. Each step starts with a number: `1.`, `2.`, etc.
2. Each step describes **one observable action or state change** — what happens, where, and what the outcome is.
3. Use concrete identifiers: function names, file paths, variable names, line numbers when known.
4. For failure findings: the final step is the **observable consequence** (crash, leak, wrong output, data exposure, etc.).
5. For counter-analysis entries: prefix with `N. COUNTER-ANALYSIS: ...` — these are numbered like regular steps.
6. For the result summary: prefix with `N. RESULT: ...` as the final numbered step.
7. **NEVER** join multiple steps into a single comma-separated string. One step per line/array entry.

### Evidence Formatting Rules

- Brief factual statements: what was observed and where.
- Bullet list or short paragraph — no numbered steps needed.
- Reference file paths and line numbers when available.

### Applies To

This distinction applies across all output types:
- **Simulator traces** (`trace` arrays): always numbered repro steps (these are execution flows by definition).
- **AttackSurfaceScanner** `trace`: ordered repro steps (these are attack flows).
- **ExploitEngineer** `trace`: ordered exploit steps. Keep `impact` as prose consequence, not steps.
- **Analyst / CodeCorrectness / Style findings**: evidence (static observations).
- **Judge verdict template**: uses `Evidence` or `Repro Steps` per finding type (see ReportTemplate.md).

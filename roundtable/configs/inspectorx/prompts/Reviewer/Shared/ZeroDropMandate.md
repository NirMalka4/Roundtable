# Zero-Drop Mandate

> **Purpose**: Judge aggregation rules preventing silent finding drops.
> **Part of**: Anti-Attention-Drift Protocol v3

---

## The Zero-Drop Principle

**Every specialist finding that enters the Judge MUST appear in the output.**

> **Scope — what "enters the Judge" means**: Zero-Drop accounts for findings a
> specialist actually **emitted** (each has a `(source_agent, finding_id)`
> tuple). It does **not** require the Judge to resurrect candidates a
> proof-gated specialist intentionally withheld upstream (e.g. SchemaDrift's
> incomplete Proof Envelope, a PenTest/ExploitEngineer non-exploitable
> candidate). Those never received a `finding_id` and are not part of
> `total_specialist_findings`. Pre-emission proof-gating is the specialist's
> firing discipline, not a drop — do not invent phantom findings to reconcile
> a specialist's low count.

The AgentDossier's concern count is the deterministic source of truth for
zero-drop verification. Each specialist concern is identified by its
`(source_agent, finding_id)` tuple and must appear in exactly one accounting
bucket. Dossier count checksum seeds the Judge's count_verification.

```
┌─────────────────────────────────────────────────────────────┐
│                    THE IRON RULE                             │
│                                                              │
│   verdict_overlay + validated_safe + needs_human_judgment    │
│                           ==                                 │
│                   total_specialist_findings                  │
│                                                              │
│   NO EXCEPTIONS. NO SILENT DROPS. NO "FILTERING OUT."        │
└─────────────────────────────────────────────────────────────┘
```

`judge_observations[]` is NOT part of this accounting. It is reserved for
Judge-native concerns that are not tied to a specialist `(source_agent,
finding_id)` tuple.

---

## Why Zero-Drop Matters

### The Problem It Solves

When processing many findings, AI models tend to:
1. **Focus on critical findings** and forget minor ones
2. **Summarize away** low-severity items
3. **Run out of output tokens** before listing everything
4. **Goal-seek to a verdict** and stop processing

### The Solution

Force explicit accounting for EVERY specialist finding:
- If it is valid and should affect the verdict → `verdict_overlay[]`
- If it is a false positive or otherwise safe → `validated_safe[]` (with explanation)
- If evidence is partial → `needs_human_judgment[]`
- **NEVER** just "not mention it"

Every specialist finding MUST appear in EXACTLY ONE of those three accounting
buckets. `judge_observations[]` may contain additional Judge-native concerns,
but it cannot be used to account for specialist work.

---

## Category-First Processing

### The Wrong Way (Severity-First)

```
❌ WRONG: Sort by severity, process critical first

1. Process CRITICAL findings (lots of explanation)
2. Process HIGH findings (running out of tokens)
3. Process MEDIUM findings (barely any detail)
4. Process LOW findings (oops, hit token limit, skip these)
```

### The Right Way (Category-First)

```
✓ CORRECT: Group by category, enumerate ALL within each

1. SECURITY category: 2 findings → enumerate both
2. LOGIC category: 5 findings → enumerate all 5
3. PERFORMANCE category: 1 finding → enumerate it
4. STYLE category: 7 findings → enumerate all 7
5. STANDARDS category: 3 findings → enumerate all 3

THEN: Assign each specialist finding to exactly one accounting bucket
```

---

## Judge Aggregation Protocol

### Step 1: Receive All Agent Findings

```yaml
input_findings:
  from_analyst:
    - finding_1
    - finding_2
    - finding_3
  from_security:
    - finding_4
    - finding_5
  from_attack_surface_scanner:
    - finding_6
  from_pentest:
    - finding_7
  from_exploit_engineer:
    - finding_8
  from_simulator:
    - breakage_1
  from_historian:
    - incident_1
  from_code_correctness:
    - finding_9
  from_architecture:
    - finding_10
  from_privacy:
    - finding_11
  from_deadlock:
    - finding_12
  from_docskeeper:
    - finding_13
  from_testquality:
    - finding_14
  from_severity_inflator:
    - inflated_1
  from_deterministic_scan:
    - flagged_1  # CANNOT be dropped
    - flagged_2  # CANNOT be dropped

  # NOTE: ALL specialist findings feed the Judge via its dedicated dossier node
  # (Dossier_Judge), which aggregates every finding-producing agent's output.
  # This list is comprehensive — every specialist finding from every agent
  # must appear in verdict_overlay[], validated_safe[], or
  # needs_human_judgment[]. Judge-native additions belong only in
  # judge_observations[] and do not change total_specialist_findings.
  total_specialist_findings: 16
```

### Step 2: Group by Category (NOT Severity)

```yaml
categorized_findings:
  security:
    - finding_4
    - finding_5
    - finding_6
  logic:
    - finding_1
    - finding_2
  redundant_operations:
    - finding_3
    - flagged_1  # From deterministic scan
  error_handling:
    - flagged_2  # From deterministic scan
```

### Step 3: Process EVERY Finding

For EACH specialist finding, the Judge MUST decide:

```
┌─────────────────────────────────────────────────────────────┐
│                  FINDING DISPOSITION TREE                    │
│                                                              │
│  Is this a REAL issue?                                       │
│  ├─ YES → Is evidence COMPLETE?                              │
│  │        ├─ YES → verdict_overlay[] (with severity)         │
│  │        └─ PARTIAL → needs_human_judgment[]                │
│  └─ NO → validated_safe[] (MUST explain why)                 │
│                                                              │
│  THERE IS NO "DROP" OPTION.                                  │
└─────────────────────────────────────────────────────────────┘
```

A specialist finding is accounted for only when its `(source_agent, finding_id)`
tuple appears in exactly one of `verdict_overlay[]`, `validated_safe[]`, or
`needs_human_judgment[]`.

### Step 4: Merge Deterministic Scan Results

Items from `FLAGGED_BY_SCAN` (deterministic pre-scan) receive special treatment:

```python
for flagged in deterministic_scan_results:
    if flagged not in categorized_findings:
        # Add to appropriate category
        categorized_findings[flagged.category].append(flagged)

    # Mark as cannot-drop
    flagged.cannot_be_dropped = True
```

### Step 5: Count Verification (MANDATORY)

Before finalizing, verify the Iron Rule invariant:
`len(verdict_overlay) + len(validated_safe) + len(needs_human_judgment) == total_specialist_findings_seen`.

If they do not match, some specialist findings are unaccounted for — re-process before output; **never** drop. Emit the proof in the `count_verification` block (see below). The single executable verification gate lives in `Shared/OutputCompleteness.md` (Gate 2 — Count Verification); do not restate it here.

---

## Output Schema Requirements

### verdict_overlay[] (Published Specialist Findings)

```yaml
verdict_overlay:
  - source_agent: "Analyst_Logic"
    finding_id: "F-001"
    title: "Null dereference on user object"
    severity: "critical"
    category: "logic"
    # ... full verdict-impacting details
```

### validated_safe[] (Dismissed Issues - MUST HAVE REASON)

```yaml
validated_safe:
  - source_agent: "Analyst_Logic"
    finding_id: "VS-001"
    what_was_flagged: "Exception catch without rethrow in ProcessData"
    why_safe: "Exception is logged with LogWarning, not silently swallowed"
    evidence: "Line 45: _logger.LogWarning(ex, 'Processing failed')"

  - source_agent: "DeterministicScan"
    finding_id: "VS-002"
    what_was_flagged: "Redundant null check at line 89"
    why_safe: "Defensive programming pattern, no functional harm"
    evidence: "Guard clause is intentional per team standard"
```

### needs_human_judgment[] (Partial Evidence)

```yaml
needs_human_judgment:
  - source_agent: "Deadlock"
    finding_id: "HJ-001"
    original_concern: "Potential race condition in UserCache"
    evidence_gathered:
      - "Found concurrent access pattern"
      - "Lock exists but scope unclear"
    evidence_gap: "Cannot determine if caller already holds lock"
    suggested_action: "PR author: please confirm thread safety of UserCache access"
```

### judge_observations[] (Judge-Native Concerns)

```yaml
judge_observations:
  - id: "JO-001"
    title: "Verdict confidence reduced by conflicting specialist evidence"
    rationale: "Two specialists disagree on reachability; this is Judge synthesis, not a specialist ref."
    zero_drop_accounting: "not_counted"
```

---

## Count Verification Output

The Judge MUST include this in every output:

```yaml
count_verification:
  total_specialist_findings_seen: 18
  published_primary: 5
  merged_subordinate: 0
  validated_safe_count: 11
  needs_human_judgment_count: 2
  judge_observations_count: 0   # Judge-native; NOT part of the invariant
  accounted_total: 18           # 5 + 0 + 11 + 2
  unclassified: 0               # total_specialist_findings_seen - accounted_total; MUST be 0
  verification_passed: true

  deterministic_scan_check:
    flagged_items: 3
    in_accounting_buckets: 3
    all_accounted: true
```

---

## Handling Token Limits

If approaching output token limit during aggregation:

### DO:
```yaml
# Truncate DETAILS, not FINDINGS
- source_agent: "Analyst_Patterns"
  finding_id: "F-015"
  title: "Magic number 3600"
  severity: "low"
  category: "style"
  description: "Magic number 3600 used as cache TTL; extract to a named constant"  # detail may be truncated in continuation records
```

### DON'T:
```yaml
# Never skip findings entirely
# ❌ "Remaining 7 low-severity findings omitted for brevity"
```

### If truly running out of space:

Set `continuation_needed: true` and resume per the **Continuation Protocol** in `Shared/OutputCompleteness.md` — record what was already processed plus the remaining `(source_agent, finding_id)` tuples so nothing is silently dropped.

---

## Deterministic Scan Backstop

Items flagged by `DeterministicPatterns.md` are cannot-drop: every flagged `(pattern_id, file:line)` MUST appear in exactly one of `verdict_overlay[]`, `validated_safe[]`, or `needs_human_judgment[]`. If a flagged item fits none, force a `judge_observations[]` entry rather than drop it. The single executable check is `verify_deterministic_backstop` in `Shared/OutputCompleteness.md` (Gate 3 — Deterministic Backstop); do not restate it here.

---

## Enforcement

The Zero-Drop Mandate is enforced at multiple levels:

| Level | Check | Action on Failure |
|-------|-------|-------------------|
| Judge | Count verification before output | Re-process missing findings |
| Orchestrator | Validate Judge output | Reject and re-run Judge |
| Publisher (deterministic, non-authoritative) | Final sanity check | Log the discrepancy; publishing proceeds — every finding is already recorded upstream, and `validated_safe` items are intentionally not posted to the PR |

---

## Anti-Patterns

### ❌ NEVER DO

```yaml
# "Filtered out 12 low-severity style issues"
# "Minor issues not shown for brevity"
# "Additional findings available in session log"
```

### ✓ ALWAYS DO

```yaml
# Every specialist finding gets explicit treatment:
verdict_overlay: [3 items]
validated_safe: [12 items with reasons]
needs_human_judgment: [2 items]
# Total: 17 = total_specialist_findings
# judge_observations may exist, but are not counted above
```

---

## Related Documents

- `Shared/AntiDriftProtocol.md` - Master protocol
- `Agents/Judge.agent.md` - Judge implementation
- `Shared/OutputCompleteness.md` - Verification thresholds

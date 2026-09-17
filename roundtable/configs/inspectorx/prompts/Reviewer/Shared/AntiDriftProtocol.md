# Anti-Attention-Drift Protocol v3

> **Purpose**: Eliminate contextual attention masking during extensive AI code reviews.
> **Status**: ACTIVE
> **Version**: 3.0
> **Date**: 2026-02-03

---

## Overview

This protocol addresses **Contextual Attention Masking** - when an LLM encounters a high-severity finding (e.g., Data Loss), the attention mechanism assigns massive weight to those tokens, "drowning out" lower-severity but still valid findings (e.g., redundant operations, naming issues).

### The Three Root Causes

| Cause | Description | Solution Layer |
|-------|-------------|----------------|
| **Dominant Signal Problem** | Attention mechanism weights critical findings heavily | Layer 1: Detection |
| **Goal-Seek Optimization** | Model stops looking after finding blockers | Layer 1: Detection |
| **Output Token Budget** | Critical explanations consume generation budget | Layer 2: Aggregation |

---

## Architecture: Three-Layer Defense

```
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 1: DETECTION                            │
│  • Sectional Sequential Passes (A/B/C)                          │
│  • Severity-Blind Enumeration                                    │
│  • Deterministic Grep Pre-Scan                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 2: AGGREGATION                          │
│  • Zero-Drop Mandate                                             │
│  • Category-First Processing (NOT severity-first)               │
│  • Finding Ownership Registry                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LAYER 3: VERIFICATION                         │
│  • Count Verification Gate                                       │
│  • Minor Finding Thresholds                                      │
│  • Re-Run Triggers                                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Detection

### 1.1 Sectional Sequential Passes

Analyst review is split by the orchestrator into three **isolated** passes (A/B/C), each run as a separate sub-agent with its own context window so findings from one pass cannot bias another. As one Analyst sub-agent, perform exactly your assigned pass below — the cross-pass isolation is handled by the orchestrator, not by you:

| Pass | Focus | Categories | Key Principle |
|------|-------|------------|---------------|
| **Pass A** | Syntax/Pattern | `redundant_operations`, `unused_variables`, `import_issues`, `syntax_anomalies`, `pattern_violations` | Find mechanical issues |
| **Pass B** | Logic/Semantic | `null_safety`, `resource_leaks`, `logic_errors`, `boundary_conditions`, `type_safety` | Find runtime bugs |
| **Pass C** | Standards/Style | `naming_conventions`, `code_clarity`, `documentation`, `maintainability` | Find quality issues |

**Critical Rule**: Each pass operates in **isolated context** - findings from Pass A MUST NOT influence Pass B or C.

### 1.2 Severity-Blind Enumeration

During detection, findings are enumerated **WITHOUT severity assessment**:

```yaml
# CORRECT: Severity-blind finding
finding:
  id: "PASS_A-001"
  locations: [{ filePath: "File.cs", startLine: 42, endLine: 43 }]
  code_does: "Calls json.loads() twice on the same input"
  ideal_code_would: "Parse JSON once and reuse the result"
  # NOTE: No severity field at this stage

# WRONG: Pre-judging severity
finding:
  id: "PASS_A-001"
  severity: "low"  # ❌ DO NOT include severity during detection
  # (remaining fields omitted)
```

**Severity is assigned LATER**, in the **terminal synthesis flow**, by SeverityInflator before Judge renders the final verdict.

### 1.3 Deterministic Pre-Scan

Before LLM analysis, run a deterministic grep scan for known anti-patterns:

```
deterministic_prescan(diff_files):
  patterns = load("Shared/DeterministicPatterns.md")
  flagged = []
  
  for file in diff_files:
    for pattern in patterns:
      matches = grep(file, pattern.regex)
      for match in matches:
        flagged.append({
          pattern_id: pattern.id,
          file: file,
          line: match.line,
          match: match.text,
          cannot_be_dropped: true  # CRITICAL: These MUST appear in output
        })
  
  return flagged
```

Items marked `FLAGGED_BY_SCAN` are **backstop findings** - they cannot be dropped regardless of AI analysis.

---

## Layer 2: Aggregation

### 2.1 Zero-Drop Mandate

**Every finding that enters the system MUST appear in the output.**

The Judge agent MUST ensure:
```
published_primary + merged_subordinate + validated_safe_count + needs_human_judgment_count == total_specialist_findings_seen
```

No finding can be silently discarded. If a finding is not actionable, it MUST appear in `validated_safe` with explicit justification.

See: `Shared/ZeroDropMandate.md` for detailed rules.

### 2.2 Category-First Processing

The Judge MUST aggregate findings **by category**, NOT by severity:

```
CORRECT Order:
1. Group all findings by category (security, logic, performance, style, standards)
2. Within each category, enumerate ALL findings
3. THEN assign final severity

WRONG Order:
1. Sort by severity
2. Process CRITICAL first
3. "Run out of tokens" before LOW findings
```

### 2.3 Finding Ownership Registry

Each pattern type has a designated **owner agent** responsible for it:

```yaml
# Example from Shared/FindingOwnershipRegistry.md
pattern: "redundant_json_parse"
regex: "json\.loads\(.*json\.loads\("
primary_owner: "Analyst_Patterns"
fallback_owner: "CodeCorrectness"
category: "redundant_operations"
```

If the primary owner misses a finding, the fallback owner catches it.

---

## Layer 3: Verification

### 3.1 Count Verification Gate

Before finalizing output, verify counts:

```python
def verify_output_completeness(input_findings, output):
    # Count verification
    total_output = (
        len(output.verdict_overlay) + 
        len(output.validated_safe) + 
        len(output.needs_human_judgment)
    )
    
    if total_output != len(input_findings):
        raise CompletionError(
            f"Missing {len(input_findings) - total_output} findings"
        )
```

### 3.2 Minor Finding Threshold

**Statistical anomaly detection**: If a review has CRITICAL findings but ZERO LOW/INFO findings, this is statistically improbable and triggers a re-run.

```python
if has_critical(output) and count_low(output) == 0:
    trigger_rerun("standards_style")  # Re-run Pass C
```

### 3.3 Deterministic Backstop Check

Every `FLAGGED_BY_SCAN` item MUST appear in output:

```python
for flagged in deterministic_scan_results:
    if flagged not in output.verdict_overlay and \
       flagged not in output.validated_safe:
        raise CompletionError(
            f"FLAGGED_BY_SCAN item dropped: {flagged.pattern_id}"
        )
```

---

## Agent Responsibilities

| Agent | Anti-Drift Responsibilities |
|-------|----------------------------|
| **Analyst** | Execute Pass A/B/C in isolated contexts; severity-blind enumeration |
| **Judge** | Zero-drop mandate; category-first processing; count verification |
| **CodeCorrectness** | Fallback owner for orphan patterns |
| **ReviewSession** | Execute deterministic pre-scan; pass `FLAGGED_BY_SCAN` to Judge |

---

## Continuation Mandate

If analysis is interrupted (token limit, timeout, etc.), the agent MUST:

1. **Output what was found so far** with a `continuation_needed: true` marker
2. **List remaining categories** that were not analyzed
3. The orchestrator will re-invoke with remaining categories

```yaml
partial_output:
  continuation_needed: true
  completed_passes: ["syntax_pattern"]
  remaining_passes: ["logic_semantic", "standards_style"]
  findings_so_far: ["PASS_A-001"]  # ids already emitted before interruption
```

---

## Related Documents

- `Shared/SectionalAnalysis.md` - Detailed templates for Pass A/B/C
- `Shared/ZeroDropMandate.md` - Judge aggregation rules
- `Shared/DeterministicPatterns.md` - Known anti-patterns for grep pre-scan
- `Shared/FindingOwnershipRegistry.md` - Pattern-to-agent mapping
- `Shared/OutputCompleteness.md` - Verification thresholds and re-run triggers

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 3.0 | 2026-02-03 | Initial implementation of three-layer architecture |
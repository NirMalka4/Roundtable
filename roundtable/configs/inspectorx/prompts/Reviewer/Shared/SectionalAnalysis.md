# Sectional Analysis Templates

> **Purpose**: Cross-pass protocol + index for the multi-pass Analyst review (attention-drift prevention).
> **Part of**: Anti-Attention-Drift Protocol v3
> **Authority**: This file is the **single source of truth** for the *cross-pass protocol* (combining,
> baseline severity assessment, continuation). Each pass's categories/template/output schema is owned by
> its own slice under `Shared/SectionalAnalysis/` (PassA/PassB/PassC), injected only into that pass's analyst.
> The Analyst pass agents (`Agents/Analyst_Patterns.agent.md`, `Agents/Analyst_Logic.agent.md`, `Agents/Analyst_Standards.agent.md`) duplicate checklists for quick reference,
> but if there is any conflict, the relevant slice (for pass categories) or THIS document (for cross-pass behavior) takes precedence.

---

## Overview

The orchestrator splits Analyst review into **three isolated passes** (A/B/C), each run as a separate sub-agent with its own context window. This isolation prevents high-severity findings from one pass from "drowning out" lower-severity findings in another pass.

**Critical Rule (severity-blind *enumeration*, not severity-free output)**: Do NOT let severity gate what you report — enumerate ALL findings first, THEN assign each a **baseline severity**. SeverityInflator *escalates* these baselines via scale/usage multipliers; it does not invent them from scratch (its schema records `original_severity → inflated_severity`).

---

## Pass Structure

```
┌─────────────────────────────────────────────────────────────┐
│                    PASS A: Syntax/Pattern                    │
│     Focus: Mechanical issues that can be found structurally │
│     Context: ISOLATED - no knowledge of Pass B/C findings   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PASS B: Logic/Semantic                    │
│     Focus: Runtime bugs that require understanding behavior │
│     Context: ISOLATED - no knowledge of Pass A/C findings   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PASS C: Standards/Style                   │
│     Focus: Quality and maintainability issues               │
│     Context: ISOLATED - no knowledge of Pass A/B findings   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    COMBINE & ENUMERATE                       │
│     All findings combined with unique IDs                    │
│     Assign BASELINE severity AFTER enumeration               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│        TERMINAL SEVERITY ESCALATION (SeverityInflator)       │
│     Take each finding's baseline severity as original_severity│
│     Then apply inflation multipliers with justification      │
└─────────────────────────────────────────────────────────────┘
```

---

## Pass Templates (per-pass slices)

Each pass's categories, checklist template, and output schema now live in a dedicated
slice that is injected ONLY into that pass's analyst, preserving the isolation the
Pass Structure above mandates (no analyst sees another pass's templates):

| Pass | Analyst agent | Slice (single source for that pass) |
|------|---------------|-------------------------------------|
| A — Syntax/Pattern | `Agents/Analyst_Patterns.agent.md` | `Shared/SectionalAnalysis/PassA.md` |
| B — Logic/Semantic | `Agents/Analyst_Logic.agent.md` | `Shared/SectionalAnalysis/PassB.md` |
| C — Standards/Style | `Agents/Analyst_Standards.agent.md` | `Shared/SectionalAnalysis/PassC.md` |

This file remains the authority for the cross-pass protocol below (combining,
baseline severity assessment, continuation). Edit a pass's categories/schema in its
slice; edit cross-pass behavior here.

---

## Combining Pass Results

After all three passes complete, combine findings:

```python
def combine_pass_results(pass_a, pass_b, pass_c):
    """
    Combine all pass results into a single enumerated list.
    """
    all_findings = []
    
    # Re-number with unified IDs
    counter = 1
    for finding in pass_a.findings:
        finding.unified_id = f"F-{counter:03d}"
        finding.source_pass = "A"
        all_findings.append(finding)
        counter += 1
    
    for finding in pass_b.findings:
        finding.unified_id = f"F-{counter:03d}"
        finding.source_pass = "B"
        all_findings.append(finding)
        counter += 1
    
    for finding in pass_c.findings:
        finding.unified_id = f"F-{counter:03d}"
        finding.source_pass = "C"
        all_findings.append(finding)
        counter += 1
    
    return all_findings
```

---

## Severity Assessment Compatibility Template

This template is retained for offline reasoning and historical docs. In the live
runtime, the terminal SeverityInflator prompt performs this baseline severity
assessment before inflation.

If you need an isolated worksheet for baseline severity review, use this shape:

```yaml
severity_assessment_input:
  findings_to_assess: [F-001, F-002, ..., F-015]  # All combined findings

severity_assessment_output:
  assessments:
    - finding_id: "F-001"
      severity: "low"
      justification: "Redundant operation has no functional impact, only minor performance cost"
    
    - finding_id: "F-002"
      severity: "critical"
      justification: "Null dereference will cause runtime exception on null user"
    
    - finding_id: "F-003"
      severity: "medium"
      justification: "Resource leak under high load could exhaust connections"
  
  count_verification:
    input_findings: 15
    assessed_findings: 15
    match: true
```

---

## Continuation Handling

If a pass is interrupted (token limit, timeout):

```yaml
partial_pass_output:
  pass_type: "logic_semantic"
  continuation_needed: true
  
  completed_categories:
    - "null_safety"
    - "resource_leaks"
  
  remaining_categories:
    - "logic_errors"
    - "boundary_conditions"
    - "type_safety"
  
  findings_so_far:
    - id: "PASS_B-001"
      locations: [{ filePath: "File.cs", startLine: 154, endLine: 156 }]
      category: "null_safety"
      code_does: "Dereferences 'user' which can be null (returned from GetUserById)"
      ideal_code_would: "Check for null before accessing user.Name"
```

The orchestrator will re-invoke the pass with remaining categories.

---

## Related Documents

- `Shared/AntiDriftProtocol.md` - Master protocol
- `Agents/Analyst_Patterns.agent.md` - Syntax/Pattern analysis pass
- `Agents/Analyst_Logic.agent.md` - Logic/Semantic analysis pass
- `Agents/Analyst_Standards.agent.md` - Standards/Style analysis pass
- `Shared/OutputCompleteness.md` - Verification that all categories are covered
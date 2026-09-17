# Output Completeness Verification

> **Purpose**: Verification thresholds and re-run triggers for anti-drift architecture.
> **Part of**: Anti-Attention-Drift Protocol v3

---

## Overview

This document defines the verification gates that ensure review output is complete. These checks run at multiple points in the pipeline to catch attention drift before it results in missing findings.

Judge output is complete only when every specialist finding is explicitly accounted for in exactly one Phase-3 outcome bucket, every Judge-native concern is recorded separately, and every required top-level field is present. Empty arrays are valid; omitted fields are not.

---

## Verification Gates

### Gate 1: Pass Completion Check (Analyst)

After each analysis pass (A/B/C), verify all categories were checked:

```python
def verify_pass_completion(pass_output):
    """
    Verify all required categories were analyzed.
    """
    required_categories = {
        "syntax_pattern": [
            "redundant_operations",
            "unused_variables", 
            "import_issues",
            "syntax_anomalies",
            "pattern_violations"
        ],
        "logic_semantic": [
            "null_safety",
            "resource_leaks",
            "logic_errors",
            "boundary_conditions",
            "type_safety"
        ],
        "standards_style": [
            "naming_conventions",
            "code_clarity",
            "documentation",
            "maintainability",
            "repo_conventions"
        ]
    }
    
    pass_type = pass_output.pass_type
    checked = pass_output.category_checklist.checked_categories
    required = required_categories[pass_type]
    
    missing = set(required) - set(checked)
    if missing:
        return {
            "complete": False,
            "missing_categories": list(missing),
            "action": "RERUN_PASS"
        }
    
    return {"complete": True}
```

### Gate 2: Count Verification (Judge)

Verify all specialist input findings are accounted for in Judge output. Judge-native observations are required when present, but they are outside the specialist overlay-count invariant.

```python
def verify_count(total_specialist_findings, output):
    """
    Verify specialist finding counts match the Phase-3 overlay-count invariant.
    """
    required_fields = [
        "verdict",
        "executive_summary",
        "verdict_overlay",
        "validated_safe",
        "needs_human_judgment",
        "judge_observations",
        "architectural_assessment",
        "plan_compliance",
        "count_verification"
    ]

    missing_fields = [field for field in required_fields if field not in output]
    if missing_fields:
        return {
            "passed": False,
            "missing_fields": missing_fields,
            "action": "REPROCESS_MISSING"
        }

    output_count = (
        len(output.verdict_overlay) +
        len(output.validated_safe) +
        len(output.needs_human_judgment)
    )
    
    if output_count != total_specialist_findings:
        return {
            "passed": False,
            "total_specialist_findings": total_specialist_findings,
            "output_count": output_count,
            "missing": total_specialist_findings - output_count,
            "action": "REPROCESS_MISSING"
        }

    count_verification = output.count_verification
    if count_verification.total_specialist_findings != total_specialist_findings:
        return {
            "passed": False,
            "reason": "count_verification total does not match input",
            "action": "REPROCESS_MISSING"
        }

    if count_verification.sum != output_count or not count_verification.passed:
        return {
            "passed": False,
            "reason": "count_verification does not prove the overlay-count invariant",
            "action": "REPROCESS_MISSING"
        }
    
    return {"passed": True}
```

### Gate 3: Deterministic Backstop Check (Judge)

Verify all `FLAGGED_BY_SCAN` items are represented in Phase-3 output:

```python
def verify_deterministic_backstop(scan_results, output):
    """
    Ensure deterministic scan items cannot be dropped.
    """
    missing = []
    
    phase3_buckets = [
        output.verdict_overlay,
        output.validated_safe,
        output.needs_human_judgment,
        output.judge_observations
    ]

    for flagged in scan_results:
        found = False
        
        for bucket in phase3_buckets:
            for item in bucket:
                searchable = str(item)
                if flagged.pattern_id in searchable or flagged.match in searchable:
                    found = True
                    break
            if found:
                break
        
        if not found:
            missing.append(flagged)
    
    if missing:
        return {
            "passed": False,
            "missing_flagged_items": missing,
            "action": "ADD_MISSING_TO_OUTPUT"
        }
    
    return {"passed": True}
```

### Gate 4: Minor Finding Threshold (Judge)

Statistical anomaly detection - if CRITICAL findings exist but no LOW findings, something is wrong:

```python
def verify_minor_finding_threshold(output):
    """
    Check for statistically improbable severity distributions.
    """
    review_concerns = list(output.verdict_overlay) + list(output.judge_observations)

    has_critical = any(
        item.severity in ["critical", "high"] or item.verdict_severity in ["critical", "high"]
        for item in review_concerns
    )
    
    low_count = sum(
        1 for item in review_concerns
        if item.severity in ["low", "info"] or item.verdict_severity in ["low", "info"]
    )
    
    # Heuristic: 10+ reviewed items with criticals but no LOW is suspicious
    total_reviewed = (
        len(output.verdict_overlay) +
        len(output.validated_safe) +
        len(output.needs_human_judgment) +
        len(output.judge_observations)
    )
    
    if has_critical and total_reviewed >= 10 and low_count == 0:
        return {
            "passed": False,
            "reason": "CRITICAL findings present but no LOW/INFO findings (statistically improbable)",
            "action": "RERUN_STANDARDS_STYLE_PASS"
        }
    
    return {"passed": True}
```

---

## Re-Run Triggers

When verification gates fail, specific re-runs are triggered:

| Trigger | Condition | Action |
|---------|-----------|--------|
| `RERUN_PASS` | Category checklist incomplete | Re-run the specific pass (A/B/C) |
| `REPROCESS_MISSING` | Count mismatch or missing required field | Judge reprocesses missing specialist findings or missing fields |
| `ADD_MISSING_TO_OUTPUT` | Deterministic items missing | Force-add to Phase-3 output |
| `RERUN_STANDARDS_STYLE_PASS` | No LOW findings with CRITICAL | Re-run Pass C |

### Re-Run Protocol

```python
def handle_verification_failure(failure):
    """
    Handle different verification failures.
    """
    if failure.action == "RERUN_PASS":
        # Re-invoke Analyst with specific pass
        return invoke_analyst(
            pass_type=failure.pass_type,
            categories=failure.missing_categories
        )
    
    elif failure.action == "REPROCESS_MISSING":
        # Re-invoke Judge with explicit list and required Phase-3 fields
        return invoke_judge(
            mode="reprocess",
            missing_findings=failure.missing_findings,
            required_fields=failure.missing_fields,
            required_top_level_fields=[
                "verdict",
                "executive_summary",
                "verdict_overlay",
                "validated_safe",
                "needs_human_judgment",
                "judge_observations",
                "architectural_assessment",
                "plan_compliance",
                "count_verification"
            ]
        )
    
    elif failure.action == "ADD_MISSING_TO_OUTPUT":
        # Force-add scan-only concerns as Judge-native observations
        for flagged in failure.missing_flagged_items:
            output.judge_observations.append({
                "id": flagged.pattern_id,
                "title": flagged.description,
                "severity": "medium",  # Default, inflator will adjust if a specialist ref exists
                "category": flagged.category,
                "description": flagged.match,
                "blocking": False,
                "locations": [{"filePath": flagged.file, "startLine": flagged.line, "endLine": flagged.line}],
                "source": "DeterministicScan",
                "forced_addition": True
            })
        return output
    
    elif failure.action == "RERUN_STANDARDS_STYLE_PASS":
        # Re-invoke Analyst Standards
        return invoke_analyst(pass_type="standards_style")
```

---

## Thresholds and Limits

### Finding Count Thresholds

| Metric | Threshold | Action if Exceeded |
|--------|-----------|-------------------|
| Max findings per pass | 50 | Split into sub-passes |
| Min findings for large PR (>20 files) | 5 | Verify, possibly re-run |
| Max validated_safe without reason | 0 | Force add reasons |
| Max needs_human_judgment without reason | 0 | Force add reasons |
| Max judge_observations without locations | 0 | Force add locations or mark as global |

### Severity Distribution Expectations

| PR Size | Expected Distribution |
|---------|----------------------|
| Small (<5 files) | 0-2 HIGH, 0-5 LOW |
| Medium (5-20 files) | 1-5 HIGH, 2-10 LOW |
| Large (>20 files) | 2-10 HIGH, 5-20 LOW |

If actual distribution deviates significantly, trigger re-run.

### Category Coverage Minimums

```yaml
expected_category_coverage:
  # At least one finding or explicit "checked, no issues" per category
  syntax_pattern:
    min_categories_checked: 5  # All 5 required
  logic_semantic:
    min_categories_checked: 5  # All 5 required
  standards_style:
    min_categories_checked: 5  # All 5 required
```

---

## Completeness Report Format

Every review MUST include a completeness report and the required Judge top-level fields:

```yaml
judge_required_top_level_fields:
  verdict: present
  executive_summary: present
  verdict_overlay: []  # May be empty, never omitted
  validated_safe: []  # May be empty, never omitted
  needs_human_judgment: []  # May be empty, never omitted
  judge_observations: []  # May be empty, never omitted
  architectural_assessment: present
  plan_compliance: present
  count_verification: present

completeness_report:
  # Gate 1: Pass Completion
  pass_completion:
    pass_a_complete: true
    pass_b_complete: true
    pass_c_complete: true
    all_categories_checked: true
  
  # Gate 2: Count Verification
  count_verification:
    total_specialist_findings_seen: 23
    published_primary: 7
    merged_subordinate: 0
    validated_safe_count: 14
    needs_human_judgment_count: 2
    judge_observations_count: 0
    accounted_total: 23
    unclassified: 0
    verification_passed: true
  
  # Gate 3: Deterministic Backstop
  deterministic_backstop:
    scan_flagged_items: 4
    items_in_phase3_output: 4
    all_accounted: true
    passed: true
  
  # Gate 4: Minor Finding Threshold
  minor_finding_threshold:
    has_critical: true
    low_count: 8
    passed: true
  
  # Overall
  all_gates_passed: true
  re_runs_performed: 0
```

---

## Continuation Protocol

If output is interrupted before completion:

```yaml
partial_output:
  continuation_needed: true
  
  # IDs below are illustrative — emit the run's actual finding IDs, never these literals
  completed:
    verdict_overlay: ["VO-001", "VO-002", "VO-003"]
    validated_safe: ["VS-001", "VS-002"]
    needs_human_judgment: ["HJ-001"]
    judge_observations: ["JO-001"]
  
  in_progress:
    current_phase3_bucket: "verdict_overlay"
    specialist_findings_processed: 5
    specialist_findings_remaining: 3
  
  not_started:
    categories: []  # All categories started
    specialist_findings: ["F-021", "F-022"]  # illustrative IDs of specialist findings not yet processed
  
  resume_from:
    type: "specialist_finding"
    source_agent: "Specialist_X"
    finding_id: "F-018"
```

### Continuation Handling

```python
def handle_continuation(partial_output):
    """
    Resume from where we left off.
    """
    if partial_output.continuation_needed:
        # Re-invoke with remaining work
        return invoke_agent(
            mode="continuation",
            completed=partial_output.completed,
            resume_from=partial_output.resume_from,
            remaining=partial_output.not_started.specialist_findings
        )
```

---

## Monitoring and Metrics

Track these metrics to detect drift patterns:

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `rerun_rate` | % of reviews needing re-runs | >15% |
| `missing_finding_rate` | Avg missing specialist findings per review | >0.5 |
| `deterministic_miss_rate` | % of scanned items initially missed | >5% |
| `severity_imbalance_rate` | % of reviews with suspicious distributions | >10% |

---

## Related Documents

- `Shared/AntiDriftProtocol.md` - Master protocol
- `Shared/ZeroDropMandate.md` - Zero-drop rules
- `Shared/SectionalAnalysis.md` - Pass definitions
- `Shared/DeterministicPatterns.md` - Pre-scan patterns
- `Agents/Judge.agent.md` - Judge implementation
# Specialist Trigger Patterns

> **Purpose**: Defines explicit trigger patterns for specialist agent invocation.
> Specialists use these patterns to determine when to run.

---

## Invocation Policies

| Policy | Behavior |
|--------|----------|
| `ALWAYS` | Run on every code review, regardless of patterns |
| `PATTERN_MANDATORY` | Run if patterns detected OR if detection is uncertain |

---

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the Schema-Drift specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## Schema-Drift Specialist Triggers

**File**: `Specialists/SchemaDrift.agent.md`
**Policy**: `PATTERN_MANDATORY`
**Uncertainty Behavior**: `RUN` (any `.sql` change without a clear DDL/proc/function marker is still investigated — better to skip noisily than miss latent ambiguity)

### High Confidence Patterns (Any match → RUN)
```yaml
schema_drift_patterns:
  # Tier 1: SQL DDL + proc/function/view/type changes (canonical trigger).
  # The agent's Discovery Protocol Phase 1 gates further inside the prompt:
  # emit findings:[] if no DDL change AND no proc/function/view/type change
  # is observed in the added/removed lines.
  #
  # v1.5: added standalone ALTER VIEW / ALTER PROCEDURE / ALTER FUNCTION
  # (separate from CREATE OR ALTER variants) per duck-validated trigger
  # expansion — covers raw-view ripple + SP-param-no-default rubrics.
  high_confidence:
    - file_pattern: "*.sql"
      content_added_or_removed:
        - regex: '\bCREATE\s+TABLE\b'
        - regex: '\bALTER\s+TABLE\b.*\bADD\b'
        - regex: '\bCREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\b'
        - regex: '\bCREATE\s+(?:OR\s+ALTER\s+)?FUNCTION\b'
        - regex: '\bCREATE\s+(?:OR\s+ALTER\s+)?VIEW\b'
        - regex: '\bALTER\s+PROCEDURE\b'        # v1.5: standalone ALTER variants
        - regex: '\bALTER\s+FUNCTION\b'
        - regex: '\bALTER\s+VIEW\b'
        - regex: '\bCREATE\s+TYPE\b.*\bAS\s+TABLE\b'
        - regex: '^\s*\[\w+\]\s+(?:INT|BIGINT|UNIQUEIDENTIFIER|NVARCHAR|VARCHAR|BIT|DATETIME2)\b'  # New column declaration line
```

### Uncertain Band (Path-only match, no content marker → still RUN)
```yaml
  uncertain:
    - file_pattern: "*.sql"
      content_added_or_removed: []  # Path matches but no DDL/proc marker — still RUN; agent emits findings:[] if Discovery Phase 1 rules it out
```

### Trigger Rule
```
RUN if:
  - ANY high_confidence file_pattern + content_marker matches, OR
  - file_pattern matches with NO content marker (uncertain band) — still RUN
The agent's prompt gates further inside: Discovery Protocol Phase 1 emits findings:[] when no DDL/proc/function/view/type change is observed.
```

---

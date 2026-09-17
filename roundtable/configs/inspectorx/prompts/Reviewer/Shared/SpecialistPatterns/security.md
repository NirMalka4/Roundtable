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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the Security specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## Security Specialist Triggers

**File**: `Specialists/Security.agent.md`
**Policy**: `ALWAYS`

No pattern matching needed - runs on every review.

---

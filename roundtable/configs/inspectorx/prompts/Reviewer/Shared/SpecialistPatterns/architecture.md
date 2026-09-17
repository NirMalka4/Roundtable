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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the Architecture specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## Architecture Specialist Triggers

**File**: `Specialists/Architecture.agent.md`
**Policy**: `ALWAYS`
**Uncertainty Behavior**: `RUN` (architecture affects all code)

Architecture specialist runs on EVERY review because:
- Code architecture issues directly impact maintainability
- SOLID violations, DRY violations, and design issues are universal concerns
- Architecture principles apply to all code regardless of patterns

No pattern matching needed - runs on every review alongside Security.

---

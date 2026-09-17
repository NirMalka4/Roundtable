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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the Code Correctness specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## Code Correctness Specialist Triggers

**File**: `Specialists/CodeCorrectness.agent.md`
**Policy**: `ALWAYS`
**Uncertainty Behavior**: `RUN` (provable defects can hide in any change)

Code Correctness runs on EVERY review because:
- Provable logic errors, resource leaks, and language-standard violations are universal risks.
- It is the **ultimate fallback owner for orphan patterns** — findings no other specialist claims
  are routed here (see `Shared/FindingOwnershipRegistry.md`).

### Focus Areas (deepen analysis when present)

These are not run/skip gates (the policy is ALWAYS); they indicate where to concentrate:

- Complex control flow: nested loops, recursion, early-return ladders, state machines.
- Resource handling: streams/handles/connections/locks acquired without guaranteed release.
- Nullability: dereferences of values that may be null/None on some path.
- Boundary conditions: off-by-one, empty/single-element collections, overflow, integer division.
- Concurrency-adjacent correctness that is not a deadlock (lost updates, check-then-act races).

### Trigger Rule
```
ALWAYS RUN on every review.
Deepen analysis when the change touches loops, resource lifetimes, nullable values,
boundary arithmetic, or is routed an orphan finding by the Judge.
```

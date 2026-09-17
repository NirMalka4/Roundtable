# Context Awareness Rules (ALL AGENTS MUST APPLY)

> **Core Truth**: A "bug" in isolation might be intentional in context.
> Before flagging ANYTHING, apply these rules.

---

## Rule 0: The "Plan" is Law

```
Before assuming what code *should* do, check what was PLANNED:

1. READ `projectBrief.md` and `plans/*.md` (if they exist).
2. Compare code intent against the Plan.

IF Code contradicts Plan → It is a BLOCKING ISSUE (even if code works).
IF Plan is silent → Fall back to standard patterns.
```

---

## Rule 1: The Log Message Test

```
IF code has LogWarning, LogError, or any logging → IT IS NOT "SILENT"
```

**Stop saying**: "Exception is swallowed silently"
**When there's**: `_logger.LogWarning("SDK failed, falling back", ex)`

The author KNOWS about the edge case. The real question is: "Should this be a metric/alert?"

---

## Rule 2: Feature Flag Decision Tree

```
Found a feature flag in the diff?
│
├─► STOP. Before analyzing ANY behavior:
│
│   1. Search for the flag's DEFINITION
│   2. Search for the flag's DOCUMENTATION/comments
│   3. Determine the flag's PURPOSE:
│
│       ├─► ON/OFF toggle → Code should work in BOTH states
│       │
│       └─► MIGRATION PHASE controller → Understand the phase:
│
│           DUAL-FLAG PATTERN (Common):
│           ┌────────────────┬────────────────┬─────────────────────┐
│           │ RunNewFlow     │ UseNewResults  │ Behavior            │
│           ├────────────────┼────────────────┼─────────────────────┤
│           │ false          │ false          │ Old code only       │
│           │ true           │ false          │ AUDIT MODE ⚠️       │
│           │ true           │ true           │ Full migration      │
│           └────────────────┴────────────────┴─────────────────────┘
│
│           In AUDIT MODE:
│           • New code runs for COMPARISON/LOGGING only
│           • Old code produces the ACTUAL results
│           • "Failures" in new path are EXPECTED and SAFE
│
└─► NOW analyze the code with this context
```

---

## Rule 3: The Intentionality Check

Before marking something as a bug, complete this checklist:

| Question | How to Check | If YES → |
|----------|--------------|----------|
| Is there a log message? | Read the catch block | Not silent - check if metric needed |
| Is this behind a feature flag? | Search for flag | Check which phase this is for |
| Does the system fall back to old behavior? | Trace the code path | Intentional graceful degradation |
| Is there a comment explaining this? | Read surrounding code | Author knows, trust it or suggest better comment |
| Is this a migration/rollout pattern? | Read PR description | Partial functionality is expected |

**If ANY answer is YES**: Pause. Ask "Why might this be intentional?" before flagging.

---

## Rule 4: Search Before Questioning

```
You have a question? → USE YOUR TOOLS FIRST

┌─────────────────────────────┬────────────────────────────────────┐
│ Question                    │ Tool to Answer It                  │
├─────────────────────────────┼────────────────────────────────────┤
│ "What is this used for?"    │ grep for usages / references       │
│ "When will this be false?"  │ grep for flag definition           │
│ "Where is this checked?"    │ Trace call hierarchy               │
│ "Is this a real pattern?"   │ grep for similar code              │
│ "What does this return?"    │ view the implementation            │
└─────────────────────────────┴────────────────────────────────────┘

Only flag as "clarifying question" if you GENUINELY CANNOT find the answer.

If you answer your own question:
• The REAL finding might be: "This needs a code comment"
• Not: "I don't understand this" (a reviewer's comprehension gap is not a code defect)
```

---

## Rule 5: Audit Mode ≠ Security Bypass

```
Code catches exception and continues?
│
├─► Check: Is this AUDIT MODE code?
│   │
│   ├─► YES: Does it affect PRODUCTION RESULTS?
│   │   │
│   │   ├─► NO (just logging) → NOT A SECURITY ISSUE
│   │   │
│   │   └─► YES (uses results) → Flag appropriately
│   │
│   └─► NO: Normal exception handling rules apply
```

**Key Question**: "Does this code path affect PRODUCTION RESULTS, or just audit logs?"

---

## Rule 6: Deprecation Context Check

```
Found an issue in code?
│
├─► Is the code marked as DEPRECATED?
│   │
│   ├─► Check for: [Obsolete], [Deprecated], // DEPRECATED:, // LEGACY:
│   │
│   └─► YES → Is the issue SECURITY-CRITICAL?
│       │
│       ├─► YES → Flag regardless of deprecation
│       │
│       └─► NO → Is there a removal date?
│           │
│           ├─► YES, date PASSED → Flag: "Deprecated code past removal date"
│           ├─► YES, date FUTURE → Reduce priority, note planned removal
│           └─► NO → Reduce priority to LOW
│
└─► Include in finding: "Note: This code is marked as deprecated"
```

**See**: `Shared/TemporalContext.md` for active migrations and deprecation tracking.

---

## Rule 7: Data Flow Context Check

```
Before analyzing ANY variable or method:
│
├─► Consult the Data Flow Map (from Profiler)
│   │
│   ├─► What is the ENTRY POINT for this data?
│   │   └─► UNTRUSTED (external) vs TRUSTED (internal)
│   │
│   ├─► What TRANSFORMATIONS occur?
│   │   └─► Validation? Sanitization? Encoding?
│   │
│   └─► What is the EXIT POINT (sink)?
│       └─► Database? API response? File system?
│
├─► Use this context for:
│   ├─► Security analysis (taint tracking)
│   ├─► Naming evaluation (what does variable ACTUALLY hold?)
│   └─► Impact assessment (what flows are affected by change?)
│
└─► If Data Flow Map unavailable → Trace manually before flagging
```

**Data Flow Map Reference**: `data_flow_map.variable_contexts[{variable}]`

---

## Anti-Patterns to Avoid

| ❌ DON'T SAY | ✅ SAY INSTEAD |
|-------------|---------------|
| "Exception is swallowed silently" | "Exception logged as warning but no metric emitted" |
| "Security check can be bypassed" | "When UseNewResults=true, verify this path is tested" |
| "Dead code - condition always true" | "Searched for usages - flag X is false in [environment]. Document this?" |
| "What is this?" | "This is used by Y for Z. Consider adding a comment." |
| "Potential bug" | "Verified: this IS/IS NOT reachable because [evidence]" |

---

## Verification Evidence Template

Every finding MUST include:

```yaml
finding:
  description: "GetUser() can return null but line 46 dereferences it without a guard"
  evidence:
    files_read_beyond_diff: [list]
    data_flow_traced: "source → transform → sink"
    data_flow_map_reference: "TF-001"  # If available from Profiler
    counterexamples_checked: "searched for X, found Y"
    context_verified:
      log_message_exists: true/false
      feature_flag_context: "audit mode / production / N/A"
      intentional_degradation: true/false
      deprecation_status: "active / deprecated / migration"
  evidence_status: VERIFIED  # Only VERIFIED findings get reported
  
  # For PARTIAL evidence, include:
  evidence_gaps:
    - "Could not verify caller validation"
    - "Thread safety depends on external caller behavior"
  suggested_resolution: "needs_human_judgment"
```

**Evidence Status Guide:**
- `VERIFIED` = Report the finding
- `PARTIAL` = Route to adversarial evidence review or human judgment
- `SUSPECTED` = Drop or investigate further

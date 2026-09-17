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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the TestQuality specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## TestQuality Specialist Triggers

**File**: `Specialists/TestQuality.agent.md`
**Policy**: `PATTERN_MANDATORY`
**Uncertainty Behavior**: `RUN` (when new code added)

### High Confidence Patterns (Any match → RUN)
```yaml
testquality_patterns:
  high_confidence:
    - file_pattern: "*Tests.cs"
    - file_pattern: "*Test.cs"
    - file_pattern: "*Spec.js"
    - file_pattern: "*spec.ts"
    - file_pattern: "test_*.py"
    - file_pattern: "*_test.py"
    - file_pattern: "*_test.go"
    - path_contains: ["Tests/", "test/", "__tests__/", "specs/"]
    - path_contains: [".Tests/", ".Test/"]
```

### Medium Confidence Patterns (Triggers coverage check)
```yaml
  medium_confidence:
    - new_file_added: true                         # New source file
    - new_public_method: true                      # New public method
    - regex: '\[Fact\]|\[Theory\]|\[Test\]'       # Test attributes
    - regex: 'describe\s*\(|it\s*\('              # JS/TS test blocks
    - regex: '@pytest|def test_'                   # Python tests
    - regex: 'func Test\w+\(t \*testing\.T\)'     # Go tests
```

### Trigger Rule
```
RUN if:
  - ANY high_confidence pattern matches (test file changed), OR
  - New source code added AND no corresponding test file exists, OR
  - Test coverage could be affected by changes
```

---

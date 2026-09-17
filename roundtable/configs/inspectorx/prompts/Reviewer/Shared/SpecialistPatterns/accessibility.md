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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the Accessibility specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## Accessibility Specialist Triggers

**File**: `Specialists/Specialist_A11y.agent.md`
**Policy**: `PATTERN_MANDATORY`
**Uncertainty Behavior**: `RUN` (when in doubt, run it)

### High Confidence Patterns (Any match → RUN)
```yaml
a11y_patterns:
  high_confidence:
    - file_extension: [".tsx", ".jsx"]                       # React component files
    - regex: '\baria-[a-z]+\s*='                             # any aria-* attribute
    - regex: '\brole\s*=\s*["''][a-z]+["'']'                 # explicit role= attribute
    - regex: '\btabIndex\s*=\s*\{?-?\d+\}?'                  # tabIndex prop
    - regex: '\bonClick\s*=\s*\{'                            # onClick handler (paired with onKeyDown audit)
    - regex: '\bonKeyDown\s*=\s*\{'                          # keyboard handler
    - regex: '\bIconButton\b'                                # Fluent UI IconButton (needs aria-label)
    - regex: '\bTooltipHost\b'                               # hover-only tooltip pattern
    - regex: '\b<(Dropdown|Combobox|ChoiceGroup|SearchBox)\b' # form controls needing labels
    - regex: '\b<(LineChart|BarChart|PieChart|VerticalBarChart|HorizontalBarChart)\b'  # charts needing role=region
    - regex: '\b<(Panel|Dialog|Modal|Callout)\b'             # dismissible surfaces (focus management)
    - regex: '\.(focus|blur)\s*\(\s*\)'                      # imperative focus calls
    - regex: '\brequestAnimationFrame\s*\('                  # post-render focus pattern
    - regex: '<h[1-6]\b'                                     # semantic heading element (heading-hierarchy)
    - regex: '\bas\s*=\s*["'']h[1-6]["'']'                   # FluentUI heading via as="hN"
    - regex: '\baria-live\s*='                               # live region (screen-reader-announcements)
    - regex: '\brole\s*=\s*["''](grid|gridcell|columnheader|rowheader)["'']'  # data-grid/table semantics
    - regex: '\b<(th|table|Divider)\b'                       # table headers / decorative separators
```

### High Confidence Patterns — Removed Lines (A11y affordance removed → MUST RUN)
```yaml
  high_confidence_removed:
    - regex: '\baria-label\s*='                              # accessible name removed
    - regex: '\bonKeyDown\s*=\s*\{'                          # keyboard handler removed
    - regex: '\brole\s*=\s*["''](radio|button|status|alert|main|toolbar|region)["'']'  # semantic role removed
    - regex: '\btabIndex\s*=\s*\{?0\}?'                      # focusability removed
    - regex: '<h[1-6]\b'                                     # heading element removed (heading-hierarchy)
    - regex: '\baria-live\s*='                               # live region removed (sr-announcements)
    - regex: '\baria-hidden\s*='                             # decorative-hide removed (sr-announcements)
```

### Medium Confidence Patterns (2+ matches → RUN)
```yaml
  medium_confidence:
    - regex: '\bonMouseEnter|onMouseLeave\b'                 # hover-only interactions (keyboard parity?)
    - regex: '\bsetTimeout\s*\(\s*[^,]+,\s*0\s*\)'           # post-render anti-pattern (should be rAF)
    - regex: '\bdangerouslySetInnerHTML\b'                   # bypasses semantic structure
    - regex: '\b<svg\b'                                      # icons may need role/title
```

### Trigger Rule
```
RUN if:
  - file_extension matches .tsx OR .jsx, OR
  - ANY high_confidence pattern matches, OR
  - 2+ medium_confidence patterns match, OR
  - Uncertainty about a11y impact exists
```

---

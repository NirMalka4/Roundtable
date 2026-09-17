# Sectional Analysis — Pass A: Syntax/Pattern

> Operational slice injected into the **Analyst Patterns** agent (Pass A only).
> Cross-pass protocol (combining, severity assessment, continuation) and the full
> multi-pass overview live in `Shared/SectionalAnalysis.md`. You run in an ISOLATED
> context: you have no knowledge of Pass B/C findings, and you only need your own pass here.

**Critical Rule (severity-blind *enumeration*, not severity-free output)**: Do NOT let severity gate what you report — enumerate ALL findings first, THEN assign each a **baseline severity**. SeverityInflator *escalates* these baselines via scale/usage multipliers; it does not invent them from scratch (its schema records `original_severity → inflated_severity`).

---

## Pass A: Syntax/Pattern Analysis

### Categories to Check

| Category | What to Look For |
|----------|------------------|
| `redundant_operations` | Duplicate calls, unnecessary transformations |
| `unused_variables` | Declared but never used, assigned but never read |
| `import_issues` | Unused imports, missing imports, circular imports |
| `syntax_anomalies` | Unusual constructs, deprecated syntax |
| `pattern_violations` | Anti-patterns, known bad patterns |
| `observability_gap` | New catch blocks with zero log/trace calls in body; new endpoints without a request metric; new I/O without surrounding trace/log context (Slice 6d.1; severity floor info, ceiling low; skip if any log call already present in scope) |

### Pass A Template

```markdown
## Pass A: Syntax/Pattern Analysis

I will analyze the code for mechanical/structural issues only.
I am NOT looking for: logic bugs, security issues, or style preferences.
I am ONLY looking for: redundant operations, unused code, import issues, syntax anomalies.

### Category Checklist

For each category below, I will examine the code and note findings:

#### 1. Redundant Operations
- [ ] Check for duplicate function calls on same input
- [ ] Check for unnecessary transformations (parse then stringify then parse)
- [ ] Check for repeated calculations that could be cached
- [ ] Check for double-null checks on same variable

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 2. Unused Variables
- [ ] Check for declared but never read variables
- [ ] Check for assigned but never used results
- [ ] Check for unused function parameters

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 3. Import Issues
- [ ] Check for unused imports
- [ ] Check for missing imports (referenced but not imported)
- [ ] Check for wildcard imports that could be specific

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 4. Syntax Anomalies
- [ ] Check for deprecated syntax
- [ ] Check for unusual constructs that might be unintended

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 5. Pattern Violations
- [ ] Check against DeterministicPatterns.md patterns
- [ ] Check for known anti-patterns in this language

Findings:
(one concrete bullet per issue found; omit if the section is clean)
```

### Output Shape

The structured output shape is defined by the OVG schema wired as this agent's
`output_schema` (surfaced as the Output Format Requirement injected at runtime).
Emit one JSON object with `pass`, `pass_type`, `files_analyzed`,
`continuation_status`, a `category_checklist` (`checked_categories` plus
`findings_per_category` counts over the categories above), and a `findings` array
whose items use `id`, `category`, `severity`, `locations`, `description`,
`evidence`, `fix`.

---

## Continuation Handling

If this pass is interrupted (token limit, timeout), emit a partial result so the orchestrator can re-invoke with the remaining categories:

```yaml
partial_pass_output:
  pass_type: "syntax_pattern"
  continuation_needed: true
  completed_categories: ["redundant_operations", "unused_variables"]
  remaining_categories: ["import_issues", "syntax_anomalies", "pattern_violations"]
  findings_so_far:
    - id: "PASS_A-001"
      locations: [{ filePath: "File.cs", startLine: 42, endLine: 43 }]
      category: "redundant_operations"
      description: "Calls json.loads() twice on the same input"
      fix: "Parse JSON once and reuse the result"
```

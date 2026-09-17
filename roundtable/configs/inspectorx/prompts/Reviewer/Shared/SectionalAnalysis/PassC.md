# Sectional Analysis — Pass C: Standards/Style

> Operational slice injected into the **Analyst Standards** agent (Pass C only).
> Cross-pass protocol (combining, severity assessment, continuation) and the full
> multi-pass overview live in `Shared/SectionalAnalysis.md`. You run in an ISOLATED
> context: you have no knowledge of Pass A/B findings, and you only need your own pass here.

**Critical Rule (severity-blind *enumeration*, not severity-free output)**: Do NOT let severity gate what you report — enumerate ALL findings first, THEN assign each a **baseline severity**. SeverityInflator *escalates* these baselines via scale/usage multipliers; it does not invent them from scratch (its schema records `original_severity → inflated_severity`).

---

## Pass C: Standards/Style Analysis

### Categories to Check

| Category | What to Look For |
|----------|------------------|
| `naming_conventions` | Inconsistent naming, misleading names |
| `code_clarity` | Hard to understand constructs, missing context |
| `documentation` | Missing comments on public APIs, outdated docs |
| `maintainability` | Magic numbers, code duplication, high complexity |
| `repo_conventions` | Deviations from rules in the `## Repo Instructions Pack` (copilot-instructions.md / `.github/instructions/*.md` / CLAUDE.md / AGENTS.md / `.editorconfig` whitelisted fields / CONTRIBUTING.md allowlisted headings). Emit ONLY when an applicable rule exists in the pack. |

### Pass C Template

```markdown
## Pass C: Standards/Style Analysis

I will analyze the code for quality and maintainability issues only.
I am NOT looking for: bugs, security issues, or runtime problems.
I am ONLY looking for: naming, clarity, documentation, team standards, maintainability.

### Category Checklist

For each category below, I will examine the code and note findings:

#### 1. Naming Conventions
- [ ] Check for inconsistent naming styles (camelCase vs PascalCase)
- [ ] Check for misleading names (variable named 'list' that's a dictionary)
- [ ] Check for single-letter variables in non-trivial scope
- [ ] Check for abbreviations that aren't universally understood

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 2. Code Clarity
- [ ] Check for overly complex expressions that should be split
- [ ] Check for nested ternaries or complex conditionals
- [ ] Check for magic numbers without explanation
- [ ] Check for boolean parameters without named arguments

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 3. Documentation
- [ ] Check for missing XML docs on public members (if required)
- [ ] Check for outdated comments that don't match code
- [ ] Check for TODO/HACK/FIXME markers

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 4. Maintainability
- [ ] Check for code duplication
- [ ] Check for methods that are too long
- [ ] Check for classes with too many responsibilities

Findings:
(one concrete bullet per issue found; omit if the section is clean)

#### 5. Repo Conventions
- [ ] Read the `## Repo Instructions Pack` section in your context
- [ ] If the section is `[NONE]`, skip this category entirely
- [ ] For each applicable rule in the pack, check whether changed code violates it
- [ ] Quote the exact rule text and name the source file (e.g. `.github/copilot-instructions.md`) in your finding evidence
- [ ] Do NOT cite rules from files listed under the pack's `### Suppressed sources` manifest

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
  pass_type: "standards_style"
  continuation_needed: true
  completed_categories: ["naming_conventions", "code_clarity"]
  remaining_categories: ["documentation", "maintainability", "repo_conventions"]
  findings_so_far:
    - id: "PASS_C-001"
      locations: [{ filePath: "File.cs", startLine: 42, endLine: 42 }]
      category: "naming_conventions"
      description: "Uses 'x' as parameter name in public method"
      fix: "Use a descriptive name like 'userId'"
```

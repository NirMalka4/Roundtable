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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the Dependency specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## Dependency Specialist Triggers

**File**: `Specialists/Dependency.agent.md`
**Policy**: `PATTERN_MANDATORY`
**Uncertainty Behavior**: `RUN` (when a dep manifest is touched, run)

### High Confidence Patterns (Any match → RUN)
```yaml
dependency_patterns:
  high_confidence:
    # JavaScript / TypeScript / Node
    - file_pattern: "package.json"
    - file_pattern: "package-lock.json"
    - file_pattern: "npm-shrinkwrap.json"
    - file_pattern: "yarn.lock"
    - file_pattern: "pnpm-lock.yaml"
    - file_pattern: "bun.lock"
    - file_pattern: "bun.lockb"
    # .NET / NuGet
    - file_pattern: "*.csproj"
    - file_pattern: "*.fsproj"
    - file_pattern: "*.vbproj"
    - file_pattern: "packages.config"
    - file_pattern: "packages.lock.json"
    - file_pattern: "Directory.Packages.props"
    - file_pattern: "paket.dependencies"
    - file_pattern: "paket.lock"
    # Python
    - file_pattern: "requirements*.txt"
    - file_pattern: "Pipfile"
    - file_pattern: "Pipfile.lock"
    - file_pattern: "pyproject.toml"
    - file_pattern: "poetry.lock"
    - file_pattern: "uv.lock"
    - file_pattern: "setup.py"
    - file_pattern: "setup.cfg"
    # Go
    - file_pattern: "go.mod"
    - file_pattern: "go.sum"
    # Rust
    - file_pattern: "Cargo.toml"
    - file_pattern: "Cargo.lock"
    # Java / Kotlin / JVM
    - file_pattern: "pom.xml"
    - file_pattern: "build.gradle"
    - file_pattern: "build.gradle.kts"
    - file_pattern: "settings.gradle"
    - file_pattern: "settings.gradle.kts"
    - file_pattern: "gradle.lockfile"
    # PHP
    - file_pattern: "composer.json"
    - file_pattern: "composer.lock"
    # Ruby
    - file_pattern: "Gemfile"
    - file_pattern: "Gemfile.lock"
    # iOS / macOS
    - file_pattern: "Podfile"
    - file_pattern: "Podfile.lock"
    # Elixir
    - file_pattern: "mix.exs"
    - file_pattern: "mix.lock"
```

### Trigger Rule
```
RUN if:
  - ANY high_confidence file_pattern matches a path in the diff (added, removed, or modified file)
No medium-confidence band. No content-based triggering — file-path-only.
```

---

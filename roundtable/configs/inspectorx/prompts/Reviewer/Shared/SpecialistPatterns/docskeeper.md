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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the DocsKeeper specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## DocsKeeper Specialist Triggers

**File**: `Specialists/DocsKeeper.agent.md`
**Policy**: `PATTERN_MANDATORY`
**Uncertainty Behavior**: `RUN` (when public API changes)

### High Confidence Patterns (Any match → RUN)
```yaml
docskeeper_patterns:
  high_confidence:
    - file_extension: [".md", ".rst", ".txt", ".adoc"]
    - path_contains: ["docs/", "documentation/", "wiki/", "README"]
    - path_contains: [".github/", "CONTRIBUTING", "CHANGELOG", "MIGRATION", "UPGRADING"]
    - file_pattern: "*.xml"  # XML documentation files
    - file_pattern: "*.yml"  # Config/doc YAML files
```

### Medium Confidence Patterns (Any match + public API → RUN)
```yaml
  medium_confidence:
    - regex: '(public|protected)\s+(class|interface|enum|struct)\s+\w+'  # Public type declaration
    - regex: '(public|protected)\s+\w+\s+\w+\s*\('                        # Public method
    - regex: '/// <summary>'                                              # XML doc comment
    - regex: '/// <param'                                                 # Parameter docs
    - regex: '/// <returns>'                                              # Return docs
    - regex: '\[Obsolete\('                                               # Deprecation
    - regex: '\/\/\/\s|\/\/!\s'                                           # Rust doc comments
    - regex: '#\[doc'                                                     # Rust doc attributes
    - regex: '@param|@returns?|@throws|@exception'                        # JSDoc/JavaDoc
    - regex: '@deprecated|@see|@example|@since|@version'                  # JSDoc metadata
    - regex: '<remarks>|<exception|<example>|<inheritdoc'                 # C# XML docs
    - regex: ':param\s|:returns?:|:rtype:|:raises?:'                      # Python docstrings
    - regex: 'Args:|Returns:|Raises:|Yields:'                             # Google-style Python docs
    - regex: 'pub\s+(fn|struct|enum|trait|type|mod)\s'                    # Rust public items
    - regex: 'export\s+(function|class|interface|type|enum|const)\s'      # JS/TS exports
    - regex: 'module\.exports'                                            # CommonJS exports
    - regex: '\[ApiController\]|\[Http(Get|Post|Put|Delete|Patch)\]'      # .NET API endpoints
    - regex: '\[Route\('                                                  # .NET routing
    - regex: '@(Get|Post|Put|Delete|Patch|Controller|RestController)\('   # Java/Spring endpoints
    - regex: '@app\.(get|post|put|delete|patch)\('                        # Python Flask/FastAPI
    - regex: '\b(swagger|openapi|operationId)\b'                          # API spec keywords
```

### Trigger Rule
```
RUN if:
  - ANY high_confidence pattern matches, OR
  - ANY medium_confidence pattern matches AND public API signature changed, OR
  - New public method/class added without documentation
```

---

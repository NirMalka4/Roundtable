# Validation Checklists

> Quick-reference checklists for Phase 1 validation and Phase 2 inversion.

---

## A. Correctness Validation

| Check | Phase 1 (Validate) | Phase 2 (Invert) |
|-------|-------------------|------------------|
| Null safety | Verify null checks exist | "What if this IS null?" |
| Bounds checking | Verify index validation | "What if array is empty?" |
| Type safety | Verify casts are safe | "What if type is wrong?" |
| Resource disposal | Verify using/try-finally | "What if dispose throws?" |
| Thread safety | Verify locks/atomicity | "What if concurrent access?" |

---

## B. String Handling Validation

**Critical for any code that normalizes, maps, or looks up string values:**

| Check | What to Verify |
|-------|---------------|
| Comparison consistency | Same method uses same comparison strategy |
| Case sensitivity | If input isn't guaranteed lowercase, use `OrdinalIgnoreCase` |
| Collection constructors | `HashSet<string>` and `Dictionary<string,T>` have `StringComparer` |
| Switch expressions | Case handling is intentional (not missing cases) |

**Red Flag**: One comparison uses `OrdinalIgnoreCase`, another uses `==` → consistency bug

---

## C. Documentation Validation

| Check | Criteria |
|-------|----------|
| Subject accuracy | Document references match its subject (not copy-pasted from elsewhere) |
| Code sample validity | "If I paste this exactly, will it work?" |
| Link validity | All URLs resolve and point to correct resources |
| Version accuracy | Version numbers match current state |

**Template Drift Detection**: Was this copy-pasted? Are ALL hardcoded values updated?

---

## D. Security Validation

| Check | What to Verify |
|-------|---------------|
| Input validation | All external inputs validated before use |
| Output encoding | Data properly encoded for context (HTML, SQL, etc.) |
| Auth checks | All endpoints verify authorization |
| Secrets handling | No secrets in logs, code, or configs |
| Dependency safety | No known vulnerable dependencies added |

---

## D2. PII/PHI Privacy Validation (ISO 27701)

| Check | What to Verify |
|-------|---------------|
| **Log content** | No PII/PHI in log messages (names, emails, IDs, health data) |
| **Telemetry events** | Custom dimensions/properties don't include PII |
| **Exception messages** | Stack traces don't contain PII from variables |
| **API responses** | Do not return more PII than the caller needs |
| **Serialization** | Sensitive fields have `[JsonIgnore]` or equivalent |
| **Error payloads** | Error responses don't leak PII (e.g., email in validation error) |
| **Storage encryption** | PII at rest is encrypted (database, cache, files) |
| **Transit encryption** | PII in transit uses TLS/mTLS |
| **Data minimization** | Code collects only PII fields needed for the feature |
| **Access scope** | No `SELECT *` on tables with PII columns |
| **Retention** | PII has defined retention/deletion mechanism |
| **Cross-boundary** | PII sent to external services is minimized and encrypted |
| **PHI safeguards** | Health data has HIPAA-compliant access controls and audit logging |

**PII Leak Detection Quick Check**:
```
Is PII being written to output?
├── Log statement? → Is PII masked/redacted?
├── API response? → Is response filtered to needed fields only?
├── Telemetry? → Are custom properties PII-free?
├── Exception? → Does message contain PII variables?
├── File/cache? → Is content encrypted?
└── All checks pass ─────────────────► OK
```

---

## E. Migration/Feature Flag Validation

| Check | Question to Answer |
|-------|-------------------|
| Flag identification | What flags exist in this diff? |
| Flag purpose | ON/OFF toggle or migration phase controller? |
| Phase understanding | Which phase is this code designed for? |
| Failure handling | Are "failures" intentional graceful degradation? |
| Logging verification | Do log messages exist before claiming "silent"? |

---

## F. Performance Validation

| Check | Threshold | Action |
|-------|-----------|--------|
| Loop complexity | O(n²) or worse | Flag if in hot path |
| Collection sizing | Large allocations | Suggest initial capacity |
| Async/await | Blocking calls in async | Flag potential deadlock |
| Database queries | N+1 patterns | Flag with evidence |

**Note**: Only flag performance issues with EVIDENCE of actual impact, not theoretical concerns.

---

## G. SOLID Principles Validation

| Principle | Check | Flag If |
|-----------|-------|---------|
| **SRP** | Count class responsibilities | >2 unrelated concerns |
| **SRP** | Constructor parameters | >5 dependencies |
| **OCP** | Switch on type/enum | Modified to add new case |
| **LSP** | Override methods | Throws NotImplementedException |
| **ISP** | Interface size | >7 methods or partial implementations |
| **DIP** | Dependency creation | Uses `new` for service dependencies |

### SRP Quick Check
```
Class has multiple reasons to change?
├── Handles data AND notifications? → Split
├── Processes AND persists? → Split
├── Validates AND transforms AND saves? → Split
└── Single cohesive purpose? → OK
```

### DIP Quick Check
```
Dependency created with "new"?
├── Is it a data object (DTO, entity)? → OK
├── Is it a service/repository? → Flag: inject via constructor
└── Is it infrastructure (HttpClient, DbConnection)? → Flag: use factory/DI
```

---

## H. Coding Standards Validation

| Check | Source | Action |
|-------|--------|--------|
| Naming conventions | Language standards | Flag violations with rule reference |
| Brace style | Project `.editorconfig` | Flag inconsistencies |
| Documentation | Team requirements | Flag missing XML docs on public APIs |
| Error handling | Codebase patterns | Flag inconsistent patterns |

### Style Consistency Check
```
Does new code match existing codebase?
├── Same naming pattern? (PascalCase for methods, camelCase for params)
├── Same formatting? (Check .editorconfig)
├── Same error handling pattern?
└── Inconsistent? → Flag with specific suggestion
```

### Documentation Requirements
```
Is this a public API?
├── YES → XML doc comment required?
│   ├── Team requires docs → Flag if missing
│   └── Team optional → Note but don't flag
└── NO (internal) → Generally skip unless complex
```

---

## Quick Decision Matrix

```
Is it a bug?
├── Can I PROVE it's reachable? ──NO──► Don't flag
├── Is there a log message? ──YES──► Not silent, check if metric needed
├── Is it behind a feature flag? ──YES──► Check which phase
├── Does it affect production? ──NO──► Lower priority
└── All checks pass ──────────────────► Flag with evidence
```

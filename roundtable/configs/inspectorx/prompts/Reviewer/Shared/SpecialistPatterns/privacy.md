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

> **Scope note**: Split from the former monolithic `SpecialistPatterns.md` so the PII/PHI Privacy specialist
> receives only its own trigger patterns. The orchestrator's real run/skip logic lives in
> `packages/core/src/context/coreServices.ts`; this file is the focus + policy reference.

---

## PII/PHI Privacy Specialist Triggers

**File**: `Specialists/Privacy.agent.md`
**Policy**: `ALWAYS`
**Uncertainty Behavior**: `RUN` (PII/PHI leakage affects all code handling user data)

Privacy specialist runs on EVERY review because:
- PII/PHI leakage is a critical compliance and legal risk (ISO 27701, GDPR, HIPAA)
- Logging, telemetry, and API responses are universal PII leak vectors
- Even non-obvious changes can introduce PII exposure (serialization, error messages)
- The cost of missing a PII leak far exceeds the cost of running a check

### Supplementary Detection Patterns (for enhanced coverage)

While this specialist runs on every review, these patterns increase analysis depth:

#### High Confidence Patterns (Trigger deep analysis)
```yaml
privacy_patterns:
  high_confidence:
    - regex: '(log|logger|_logger|Log|Logger)\.(Debug|Info|Warn|Error|Fatal|Trace|Information|Warning|Critical)\s*\([^)]*\b(email|Email|name|Name|ssn|SSN|phone|Phone|address|Address|dateOfBirth|DateOfBirth|patientId|PatientId|socialSecurity|nationalId)\b'
    - regex: '(Track\w+|AddProperty|SetCustomProperty|customDimensions)\s*\([^)]*\b(email|name|phone|userId|patientId|ssn)\b'
    - regex: '(JsonConvert\.Serialize|JsonSerializer\.Serialize|json\.dumps|JSON\.stringify)\s*\(\s*(user|patient|person|customer|employee|member|profile|account)'
    - regex: 'SELECT\s+\*\s+FROM\s+\[?(users?|patients?|customers?|employees?|members?|profiles?|accounts?)\]?'
    - regex: '\b(diagnosis|medication|treatment|prescription|medical_record|health_plan|lab_result|insurance_id|mrn|icd_code)\b'
    - regex: 'public\s+(string|int|DateTime|Guid)\s+(SSN|SocialSecurityNumber|Password|PasswordHash|CreditCard|BankAccount|DateOfBirth|NationalId|PassportNumber|DriversLicense)\s*\{'
```

#### Medium Confidence Patterns (Trigger data flow tracing)
```yaml
  medium_confidence:
    - regex: '\b(user\.Email|user\.Name|user\.Phone|user\.Address|patient\.)\b'
    - regex: '\b(PersonalData|SensitiveData|PiiData|ProtectedHealth|Phi|Pii)\b'
    - regex: '\b(encrypt|decrypt|mask|redact|anonymize|pseudonymize|hash)\b'  # Verify correct usage
    - regex: '\b(gdpr|hipaa|privacy|consent|dataSubject|dataProtection)\b'  # Privacy-related code
    - file_extension: [".sql", ".graphql"]  # Query files accessing user data
    - path_contains: ["models/", "entities/", "dto/", "viewmodels/"]  # Data models
```

### Trigger Rule
```
ALWAYS RUN on every review.
Additional depth triggered when:
  - ANY high_confidence pattern matches → Deep data flow tracing
  - ANY medium_confidence pattern matches → Extended PII field analysis
  - Changes touch data models, API controllers, or logging infrastructure
```

No pattern matching needed for invocation - runs on every review alongside Security.

---

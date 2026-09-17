# Deterministic Patterns for Pre-Scan

> **Purpose**: Known anti-patterns for grep-based pre-scan before LLM analysis.
> **Part of**: Anti-Attention-Drift Protocol v3
> **Note**: This is NOT a linter. These are high-signal patterns that MUST appear in output.
> **Non-injected design reference**: This file is documentation only. It is **not** injected into any agent at runtime (no allowlisted frontmatter key references it) and is **not** loaded by the deterministic pre-scan code — the runtime pre-scan patterns live in TypeScript. It is retained as a design reference.

---

## Overview

The deterministic pre-scan runs **before** any LLM analysis to establish a **backstop** - findings that cannot be dropped regardless of what the AI discovers. Items flagged here are marked `FLAGGED_BY_SCAN` and MUST appear in the final output.

---

## Pattern Registry

### Category: Redundant Operations

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `RO-001` | Double JSON Parse | `json\.loads\s*\(\s*json\.loads\s*\(` | Parsing JSON twice on same input | Python |
| `RO-002` | Double JSON Parse (.NET) | `JsonConvert\.Deserialize.*JsonConvert\.Deserialize` | Parsing JSON twice on same input | C# |
| `RO-003` | Double ToString | `\.ToString\(\)\.ToString\(\)` | Redundant ToString chain | C#, Java |
| `RO-004` | Double await | `await\s+await\s+` | Awaiting an already awaited task | C#, JS, TS |
| `RO-005` | Repeated null check | `if\s*\(\s*(\w+)\s*!=\s*null\s*\)\s*\{[^}]*if\s*\(\s*\1\s*!=\s*null\s*\)` | Checking same variable for null twice | C#, Java |

### Category: Error Handling

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `EH-001` | Empty catch block | `catch\s*\([^)]*\)\s*\{\s*\}` | Exception swallowed silently | C#, Java |
| `EH-002` | Catch with only comment | `catch\s*\([^)]*\)\s*\{\s*//[^\n]*\s*\}` | Exception handler only has comment | C#, Java |
| `EH-003` | Generic exception catch | `catch\s*\(\s*Exception\s+\w+\s*\)` | Catching overly broad exception | C#, Java |
| `EH-004` | Bare except (Python) | `except\s*:` | Catching all exceptions including system | Python |

### Category: Resource Leaks

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `RL-001` | HttpClient in method | `new\s+HttpClient\s*\(` | HttpClient should be singleton/pooled | C# |
| `RL-002` | Stream without using | `new\s+(File|Memory|Network)Stream\s*\([^;]*;(?!.*using)` | Stream created without using statement | C# |
| `RL-003` | SqlConnection without using | `new\s+SqlConnection\s*\([^;]*;(?!.*using)` | Connection created without using | C# |

### Category: Security

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `SEC-001` | Hardcoded password | `(password|pwd|passwd)\s*=\s*["'][^"']+["']` | Credential in source code | All |
| `SEC-002` | Hardcoded API key | `(api[_-]?key|apikey)\s*=\s*["'][a-zA-Z0-9]{16,}["']` | API key in source code | All |
| `SEC-003` | SQL concatenation | `["']\s*\+\s*\w+\s*\+\s*["'].*(?:SELECT|INSERT|UPDATE|DELETE)` | Potential SQL injection | All |
| `SEC-004` | Disabled certificate validation | `ServerCertificateValidationCallback\s*=.*true` | Certificate validation bypassed | C# |

### Category: Null Safety

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `NS-001` | Null-forgiving after FirstOrDefault | `\.FirstOrDefault\(\)!` | Null suppression on nullable result | C# |
| `NS-002` | Null-forgiving after Find | `\.Find\([^)]*\)!` | Null suppression on nullable result | C# |
| `NS-003` | Chained null access | `\.\w+\?\.\w+\?\.\w+\?\.` | Triple+ null-conditional chain | C# |

### Category: Async/Concurrency

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `AC-001` | .Result on Task | `\.Result\b(?!\s*\))` | Blocking on async (potential deadlock) | C# |
| `AC-002` | .Wait() on Task | `\.Wait\(\)` | Blocking on async (potential deadlock) | C# |
| `AC-003` | async void | `async\s+void\s+\w+` | Fire-and-forget without error handling | C# |
| `AC-004` | lock on this | `lock\s*\(\s*this\s*\)` | Locking on publicly accessible object | C# |

### Category: Code Smells

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `CS-001` | TODO in production | `//\s*TODO:?\s+` | Unfinished work marker | All |
| `CS-002` | HACK marker | `//\s*HACK:?\s+` | Known workaround marker | All |
| `CS-003` | FIXME marker | `//\s*FIXME:?\s+` | Known bug marker | All |
| `CS-004` | Magic number | `(?<!\d)(?:404|500|200|3600|86400|1000|60000)(?!\d)` | Common magic numbers | All |

### Category: PII/PHI Privacy

| ID | Pattern | Regex | Description | Languages |
|----|---------|-------|-------------|-----------|
| `PII-001` | PII in log statement | `(log\|logger\|_logger\|Log\|Logger)\.\w+\([^)]*\b(email\|Email\|ssn\|SSN\|phone\|Phone\|dateOfBirth\|DateOfBirth\|patientId\|PatientId\|socialSecurity\|nationalId)\b` | PII field referenced in log call | All |
| `PII-002` | PII in telemetry | `(Track\w+\|AddProperty\|SetCustomProperty\|customDimensions)\s*\([^)]*\b(email\|name\|phone\|userId\|patientId\|ssn)\b` | PII in analytics/telemetry event | All |
| `PII-003` | Full user object serialized | `(JsonConvert\.Serialize\|JsonSerializer\.Serialize\|json\.dumps\|JSON\.stringify)\s*\(\s*(user\|patient\|person\|customer\|employee\|member\|profile\|account)` | Serializing entire PII-bearing object | All |
| `PII-004` | SELECT * on PII table | `SELECT\s+\*\s+FROM\s+\[?(users?\|patients?\|customers?\|employees?\|members?\|profiles?\|accounts?)\]?` | Selecting all columns from PII table | All |
| `PII-005` | PHI field access in code | `\b(diagnosis\|medication\|treatment\|prescription\|medical_record\|health_plan\|lab_result\|insurance_id\|mrn)\b` | Protected Health Information field referenced | All |
| `PII-006` | Sensitive property without [JsonIgnore] | `public\s+(string\|int\|DateTime\|Guid)\s+(SSN\|SocialSecurityNumber\|Password\|PasswordHash\|CreditCard\|BankAccount\|NationalId\|PassportNumber\|DriversLicense)\s*\{` | Sensitive property exposed in serializable model | C# |

---

## Usage in Pre-Scan

```python
def run_deterministic_prescan(diff_files: list[str]) -> list[FlaggedItem]:
    """
    Run before LLM analysis to establish backstop findings.
    """
    patterns = load_patterns("Shared/DeterministicPatterns.md")
    flagged = []
    
    for file in diff_files:
        content = view(file)
        language = detect_language(file)
        
        for pattern in patterns:
            if language not in pattern.languages and "All" not in pattern.languages:
                continue
                
            for match in regex_findall(pattern.regex, content):
                flagged.append({
                    "pattern_id": pattern.id,
                    "file": file,
                    "line": get_line_number(content, match),
                    "match": match.group(),
                    "description": pattern.description,
                    "category": pattern.category,
                    "cannot_be_dropped": True
                })
    
    return flagged
```

---

## Output Schema

```yaml
deterministic_scan_result:
  scan_type: "grep_pattern"
  scan_timestamp: "2026-02-03T22:00:00Z"
  patterns_checked: ["RO-001", "RO-002", ...]
  files_scanned: 42
  
  flagged_items:
    - pattern_id: "RO-001"
      file: "Services/DataProcessor.py"
      line: 156
      match: "json.loads(json.loads(data))"
      description: "Parsing JSON twice on same input"
      category: "redundant_operations"
      cannot_be_dropped: true
    
    - pattern_id: "EH-001"
      file: "Controllers/ApiController.cs"
      line: 89
      match: "catch (Exception ex) { }"
      description: "Exception swallowed silently"
      category: "error_handling"
      cannot_be_dropped: true
```

---

## Adding New Patterns

When adding patterns, ensure:

1. **High Signal**: Pattern should have >90% true positive rate
2. **Language Specific**: Mark which languages the regex applies to
3. **Testable**: Include at least 3 positive and 3 negative test cases
4. **Non-Overlapping**: Don't duplicate existing linter rules
5. **Documented**: Explain why this pattern matters

### Pattern Template

```yaml
- id: "XX-NNN"
  name: "Pattern Name"
  regex: "your_regex_here"
  description: "What this pattern catches"
  languages: ["C#", "Java"]
  category: "category_name"
  severity_hint: "high"  # Hint only, actual severity assigned by AI
  test_positive:
    - "code that should match"
  test_negative:
    - "code that should not match"
```

---

## Important Notes

1. **NOT a Linter**: These patterns are high-confidence, high-signal issues that need explicit handling
2. **Backstop Only**: The AI may find additional issues; these are the minimum
3. **Cannot Be Dropped**: Items flagged here MUST appear in final output (verdict_overlay[] OR validated_safe)
4. **Severity Flexible**: The pattern provides a hint, but final severity is assigned by SeverityInflator

---

## Related Documents

- `Shared/AntiDriftProtocol.md` - Master protocol
- `Shared/FindingOwnershipRegistry.md` - Pattern-to-agent mapping
- `Shared/OutputCompleteness.md` - Verification that flagged items aren't dropped
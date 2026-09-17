# Finding Ownership Registry

> **Purpose**: Pattern-to-agent ownership mapping to ensure no finding is orphaned.
> **Part of**: Anti-Attention-Drift Protocol v3

---

## Overview

Each finding pattern has a **primary owner** agent responsible for detecting it. If the primary owner misses a finding (due to attention drift or context limits), the **fallback owner** catches it.

This registry ensures:
1. Every pattern type has clear ownership
2. No findings fall through the cracks
3. The CodeCorrectness agent serves as the ultimate fallback

---

## Ownership Registry

### Security Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `injection_sql` | SQL injection vulnerabilities | Security | AttackSurfaceScanner | security |
| `injection_xss` | Cross-site scripting | Security | AttackSurfaceScanner | security |
| `injection_command` | Command injection | Security | AttackSurfaceScanner | security |
| `auth_bypass` | Authentication bypass | Security | AttackSurfaceScanner | security |
| `auth_weak` | Weak authentication | Security | AttackSurfaceScanner | security |
| `secrets_hardcoded` | Hardcoded credentials | Security | Analyst | security |
| `crypto_weak` | Weak cryptography | Security | AttackSurfaceScanner | security |
| `input_unsanitized` | Unsanitized user input | Security | CodeCorrectness | security |
| `exploitability` | Exploitability assessment of findings | PenTest | AttackSurfaceScanner | security |
| `exploit_chain` | Multi-step exploit scenarios | ExploitEngineer | PenTest | security |

### PII/PHI Privacy Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `pii_in_logs` | PII/PHI data in log statements | Privacy | Security | privacy |
| `pii_in_telemetry` | PII/PHI in telemetry/analytics events | Privacy | Security | privacy |
| `pii_in_response` | PII/PHI leaking in API responses | Privacy | Security | privacy |
| `pii_in_exception` | PII/PHI in exception messages or stack traces | Privacy | CodeCorrectness | privacy |
| `pii_unencrypted` | PII/PHI stored/transmitted without encryption | Privacy | Security | privacy |
| `pii_over_collection` | Collecting more PII than necessary (data minimization) | Privacy | Architecture | privacy |
| `pii_no_masking` | PII/PHI displayed or serialized without masking/redaction | Privacy | Security | privacy |
| `pii_cross_boundary` | PII/PHI sent across trust boundaries without protection | Privacy | Security | privacy |
| `phi_unprotected` | Protected Health Information without HIPAA safeguards | Privacy | Security | privacy |
| `pii_retention_risk` | PII stored without defined retention/deletion policy | Privacy | Architecture | privacy |
| `pii_select_star` | SELECT * on tables containing PII/PHI columns | Privacy | Analyst | privacy |
| `pii_missing_annotation` | Sensitive properties missing [JsonIgnore] or equivalent | Privacy | CodeCorrectness | privacy |

### Logic Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `null_dereference` | Null reference exceptions | Analyst | CodeCorrectness | logic |
| `off_by_one` | Off-by-one errors | Analyst | CodeCorrectness | logic |
| `boundary_overflow` | Array/buffer boundary issues | Analyst | CodeCorrectness | logic |
| `logic_inversion` | Inverted boolean logic | Analyst | CodeCorrectness | logic |
| `type_mismatch` | Type casting errors | Analyst | CodeCorrectness | logic |
| `condition_always` | Always true/false conditions | Analyst | CodeCorrectness | logic |
| `unreachable_code` | Dead code paths | Analyst | CodeCorrectness | logic |

### Resource Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `resource_leak` | Undisposed resources | Analyst | CodeCorrectness | resource |
| `connection_leak` | Database/network connection leaks | Analyst | CodeCorrectness | resource |
| `stream_unclosed` | Unclosed streams | Analyst | CodeCorrectness | resource |
| `memory_unbounded` | Unbounded memory growth | Analyst | CodeCorrectness | resource |
| `file_handle_leak` | File handle leaks | Analyst | CodeCorrectness | resource |

### Concurrency Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `race_condition` | Race conditions | Deadlock | Analyst | concurrency |
| `deadlock_potential` | Potential deadlocks | Deadlock | Analyst | concurrency |
| `lock_inversion` | Lock ordering violations | Deadlock | Analyst | concurrency |
| `async_void` | Fire-and-forget without handling | Analyst | CodeCorrectness | concurrency |
| `blocking_async` | Blocking on async (.Result, .Wait) | Analyst | CodeCorrectness | concurrency |
| `thread_unsafe_collection` | Thread-unsafe collection access | Deadlock | Analyst | concurrency |

### Redundancy Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `redundant_operation` | Duplicate operations | Analyst | CodeCorrectness | redundant_operations |
| `redundant_json_parse` | Double JSON parsing | Analyst | CodeCorrectness | redundant_operations |
| `redundant_null_check` | Repeated null checks | Analyst | CodeCorrectness | redundant_operations |
| `redundant_await` | Double await | Analyst | CodeCorrectness | redundant_operations |
| `redundant_cast` | Unnecessary type casts | Analyst | CodeCorrectness | redundant_operations |

### Error Handling Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `exception_swallowed` | Silent exception handling | Analyst | CodeCorrectness | error_handling |
| `exception_generic` | Overly broad exception catch | Analyst | CodeCorrectness | error_handling |
| `exception_rethrow_wrong` | Incorrect exception rethrow | Analyst | CodeCorrectness | error_handling |
| `error_ignored` | Return value ignored | Analyst | CodeCorrectness | error_handling |

### Style/Standards Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `naming_violation` | Naming convention violations | Analyst | CodeCorrectness | style |
| `magic_number` | Hardcoded magic numbers | Analyst | Architecture | style |
| `code_duplication` | Duplicated code blocks | Architecture | Analyst | style |
| `complexity_high` | High cyclomatic complexity | Architecture | Analyst | style |
| `documentation_missing` | Missing required documentation | DocsKeeper | Analyst | standards |

### Test Patterns

| Pattern | Description | Primary Owner | Fallback Owner | Category |
|---------|-------------|---------------|----------------|----------|
| `test_missing` | Missing test coverage | TestQuality | Analyst | testing |
| `test_assertion_missing` | Test without assertions | TestQuality | CodeCorrectness | testing |
| `test_flaky` | Flaky test indicators | TestQuality | CodeCorrectness | testing |

---

## Ownership Lookup

Look up a pattern's `primary_owner` and `fallback_owner` in the Ownership Registry table above. Patterns not listed default to **CodeCorrectness** (primary) and **Analyst_Patterns** (fallback). A finding missed by its primary owner is routed to that pattern's fallback owner.

---

## Fallback Chain

When a finding is not caught by its primary owner:

```
Primary Owner (missed)
        │
        ▼
Fallback Owner (second chance)
        │
        ▼
CodeCorrectness (ultimate fallback for all patterns)
        │
        ▼
Deterministic Pre-Scan (if pattern is in DeterministicPatterns.md)
```

---

## CodeCorrectness: The Ultimate Fallback

The **CodeCorrectness** specialist agent has a special role:

1. It is the **fallback owner** for most pattern types
2. It runs on **every** review (invocation_policy: ALWAYS)
3. It should catch any findings that slipped through other agents
4. It specifically watches for patterns from `DeterministicPatterns.md`

See: `Agents/Specialists/CodeCorrectness.agent.md` for implementation details.

---

## Adding New Patterns

When adding a new pattern to the registry:

1. **Identify the best primary owner** based on the pattern's nature
2. **Assign a fallback owner** (usually an adjacent specialist or CodeCorrectness)
3. **Assign a category** for aggregation purposes
4. **Document the pattern** in this registry
5. **If high-signal**: Also add to `DeterministicPatterns.md`

### Pattern Entry Template

```yaml
- pattern: "pattern_id"
  description: "What this pattern detects"
  primary_owner: "AgentName"
  fallback_owner: "FallbackAgent"
  category: "category_name"
  detection_method: "static" | "semantic" | "both"
  example: "Code example that triggers this pattern"
```

---

## Ownership Verification

The Judge verifies coverage: every finding's source agent must be the pattern's primary owner, its fallback owner, or **CodeCorrectness**. Findings from an unexpected source are logged for review — never silently dropped.

---

## Related Documents

- `Shared/AntiDriftProtocol.md` - Master protocol
- `Shared/DeterministicPatterns.md` - Patterns for grep pre-scan
- `Agents/Specialists/CodeCorrectness.agent.md` - Ultimate fallback agent
- `Shared/ZeroDropMandate.md` - Ensuring no findings are dropped
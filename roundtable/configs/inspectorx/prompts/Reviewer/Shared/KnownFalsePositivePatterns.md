# Known False Positive Patterns

> **Purpose**: Aggregated patterns that commonly produce false positives.
> Agents MUST check this file before flagging any finding that matches these patterns.

---

## ⚠️ MANDATORY CHECK

Before reporting ANY finding, verify you're not hitting a known false positive pattern:

```
FOR EACH FINDING:
│
├─► Does this match a pattern in this file?
│   │
│   ├─► YES → Apply the documented mitigation
│   │   │
│   │   └─► Still an issue after mitigation?
│   │       ├─► YES → Flag with evidence showing mitigation doesn't apply
│   │       └─► NO → DROP the finding
│   │
│   └─► NO → Proceed with normal flagging
```

---

## Pattern Registry

### FP-001: Null Reference Without Caller Context

**Category**: `null_reference_without_caller_check`  
**Occurrences**: Common  
**Agents Affected**: Analyst_Logic

**Common Cause**: Agent flags potential null dereference without checking if callers validate input before the call.

**False Positive Scenario**:
```csharp
// Agent sees this and flags "potential null reference"
public void ProcessUser(User user)
{
    var name = user.Name;  // ← Agent flags this
}

// But misses that ALL callers validate:
public void HandleRequest(UserRequest request)
{
    if (request.User == null) throw new ArgumentNullException();
    ProcessUser(request.User);  // ← User is guaranteed non-null here
}
```

**Mitigation**: Before flagging null reference:
1. Read at least ONE caller of the method
2. Check if the caller validates the parameter before passing
3. Check if the method is internal/private (callers are limited)
4. Only flag if null can actually reach the dereference point

---

### FP-002: Thread Safety False Alarm

**Category**: `thread_safety_false_alarm`  
**Occurrences**: Common  
**Agents Affected**: Deadlock, Analyst_Logic

**Common Cause**: Agent sees collection modification in a loop but doesn't verify if the collections are actually the same or shared.

**False Positive Scenario**:
```csharp
// Agent flags "modifying collection during iteration"
foreach (var sdk in sdkList)
{
    subList.Add(sdk.SubItems);  // ← Agent flags this
}
// But sdkList ≠ subList - completely different collections!
```

**Mitigation**: Before flagging thread safety:
1. Verify the collections being modified are ACTUALLY the same
2. Trace the collection reference to confirm sharing
3. Check if the code is single-threaded (no concurrent access)

---

### FP-003: SQL Injection - Parameterized Query

**Category**: `sql_injection_parameterized`  
**Occurrences**: Common  
**Agents Affected**: Security, AttackSurfaceScanner

**Common Cause**: Agent sees string concatenation near database code and assumes SQL injection without checking if the query is actually parameterized.

**False Positive Scenario**:
```csharp
// Agent sees a variable near the query and flags "SQL injection risk"
var query = "SELECT * FROM Users WHERE Id = @userId";
// But it is parameterized via Dapper — @userId is bound, not concatenated:
var users = connection.Query<User>(query, new { userId });  // ← Safe!
```

> ⚠️ Counter-example (NOT safe): `var query = $"... WHERE Id = {userId}";` — string
> interpolation inlines the value into the SQL text, so passing `new { userId }` does
> nothing. That **is** injection. Safety requires a `@param` placeholder as above.

**Mitigation**: Before flagging SQL injection:
1. Check if the concatenated string is used with an ORM that parameterizes
2. Look for `@param` syntax, `new { }` anonymous objects, `SqlParameter` usage
3. Verify the string actually reaches raw SQL execution

---

### FP-004: Exception "Swallowed" But Logged

**Category**: `exception_handled_logged`  
**Occurrences**: Very Common  
**Agents Affected**: Analyst_Logic

**Common Cause**: Agent sees catch block without rethrow and calls it "swallowed" but misses logging.

**False Positive Scenario**:
```csharp
try { /* code */ }
catch (Exception ex)
{
    _logger.LogWarning(ex, "Operation failed, falling back");  // ← NOT swallowed!
    return fallbackValue;
}
```

**Mitigation**: Before flagging swallowed exception:
1. Check for ANY logging in the catch block (LogWarning, LogError, etc.)
2. If logged → NOT silent, consider if metric is needed instead
3. Only flag truly empty catch blocks with no logging

---

### FP-005: Audit Mode Code

**Category**: `audit_mode_not_production`  
**Occurrences**: Common in migration scenarios  
**Agents Affected**: Security, AttackSurfaceScanner, ExploitEngineer, Analyst_Logic

**Common Cause**: Agent flags code behavior without realizing it only runs in audit mode and doesn't affect production results.

**False Positive Scenario**:
```csharp
if (config.RunNewFlow)  // Audit mode flag
{
    try { newImplementation.Process(); }
    catch { /* ← Agent flags "security bypass" */ }
}
// Production results come from OLD path when UseNewResults=false
return oldImplementation.GetResults();
```

**Mitigation**: Before flagging security/correctness issues:
1. Check if code is behind a feature flag
2. Determine if the flag controls AUDIT mode vs PRODUCTION mode
3. If audit mode → the code doesn't affect production results
4. Only flag if issue affects production path

---

### FP-006: Test/Placeholder Data Unreachable

**Category**: `test_data_unreachable`  
**Occurrences**: Common  
**Agents Affected**: Security, SeverityInflator

**Common Cause**: Agent flags "test data in production" without checking if the pattern can ever match real data.

**False Positive Scenario**:
```csharp
// Agent flags "hardcoded test data exposure"
if (cpe.StartsWith("vendor:product"))  // ← Can't match real CPE
{
    return UnsupportedResult;
}
// Real CPEs look like: "cpe:2.3:a:microsoft:windows:10:*"
```

**Mitigation**: Before flagging test/example data:
1. Apply the Reachability Test: Can this pattern match real production data?
2. Patterns like `vendor:product`, `example.com`, `foo`, `bar` → UNREACHABLE
3. If unreachable → LOW/style issue at most, not security finding

---

### FP-007: Breaking Change Without Consumers

**Category**: `breaking_change_no_consumers`  
**Occurrences**: Occasional  
**Agents Affected**: Analyst_Patterns

**Common Cause**: Agent flags "breaking change" for API modification without checking if anyone actually uses the old API.

**False Positive Scenario**:
```csharp
// Agent flags "breaking change - method signature changed"
// OLD: public void Process(string input)
// NEW: public void Process(string input, bool validate = true)

// But: Method is internal, only one caller, caller updated in same PR
```

**Mitigation**: Before flagging breaking change:
1. Search for usages of the changed API
2. Check if all callers are updated in the same PR
3. Check if API is internal/private (limited blast radius)
4. Only flag if external consumers would break

---

### FP-008: Performance Issue in Cold Path

**Category**: `performance_cold_path`  
**Occurrences**: Common  
**Agents Affected**: Analyst_Patterns, SeverityInflator

**Common Cause**: Agent flags performance issues without considering if the code is in a hot path or rarely executed.

**False Positive Scenario**:
```csharp
// Agent flags "inefficient string concatenation in loop"
// But this runs once at startup for 5 items:
foreach (var config in startupConfigs)  // startupConfigs.Count == 5
{
    connectionString += config.Value;  // ← Not a real perf issue
}
```

**Mitigation**: Before flagging performance:
1. Determine if code is in a hot path (frequently executed)
2. Check loop bounds - is this 5 iterations or 5 million?
3. One-time startup code ≠ request-handling code
4. Only flag performance issues with actual impact

---

### FP-009: Editorial & Cosmetic Nitpicks on Non-Behavioral Artifacts

**Category**: `cosmetic_nitpick`  
**Occurrences**: Very Common  
**Agents Affected**: Analyst_Standards, DocsKeeper

**Common Cause**: Agent surfaces findings about grammar, spelling, vocabulary choice, comment wording, or documentation style that have zero effect on runtime behavior. These are subjective editorial preferences, not engineering concerns.

**Typical Examples**:
- Typos or grammar in changelogs, commit messages, or code comments
- Naming vocabulary preferences (e.g., "'big' should be 'oversized'" or "'handle' should be 'skip'")
- Comment label accuracy (e.g., "comment says 'fail fast' but behavior is graceful degradation")
- Missing doc comments on internal/non-public-API fields
- Changelog entries being "vague" or not detailed enough

**Why Noise**: These findings share a common trait — changing them has no effect on correctness, security, or runtime behavior. They are subjective style preferences that dilute the signal of a code review. The author chose these words intentionally; a reviewer's vocabulary preference is not a defect.

**Mitigation**: Before flagging any editorial/cosmetic issue:
1. Does it affect **runtime behavior**, correctness, or security? → Flag it
2. Is the text **actively misleading** in a way that could cause misuse or bugs (e.g., a field named `delete` that archives, or a comment documenting the wrong algorithm)? → Flag as LOW/INFO
3. Is it purely cosmetic — grammar, spelling, vocabulary preference, comment wording, doc completeness on internal fields? → **DROP** — not an engineering concern

---

## Adding New Patterns

> **Related Documents**:
> - `Shared/ContextAwareness.md` - Rules 1-5 cover context-based false positive prevention (feature flags, audit mode, logging)
> - `Shared/PragmaticReview.md` - General philosophy on avoiding noise

When a new false positive pattern is identified:

1. **Confirm it recurs** — the same false positive seen 3+ times across reviews
2. **Create an entry here** with:
   - Unique ID (FP-XXX)
   - Category name
   - Affected agents
   - Clear false positive scenario
   - Specific mitigation steps
3. **Update agent instructions** to reference the new pattern

---

## Pattern Review Cadence

- **Weekly**: Review recent review sessions for recurring false-positive patterns
- **Monthly**: Audit this file for pattern effectiveness
- **Quarterly**: Archive patterns with 0 occurrences in 3 months


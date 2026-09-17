# Pragmatic Review Standards

> **Mission**: Be USEFUL, not theoretical. Provide VALUE, not noise.

---

## The Human Reviewer Standard

Before publishing any finding, ask: **"Would YOU flag this?"**

### Transparency Principle: Pragmatic != Silent
"Pragmatic" means **categorizing correctly**, not **hiding information**.
*   **BAD**: Hiding a valid minor issue to avoid "noise".
*   **GOOD**: Reporting the minor issue but tagging it as `[Nitpick]` or `low Severity`.
*   **WHY**: The developer (not the AI) owns the prioritization. Give them the data.

### The Helper Principle
Don't just be a Critic; be a **Co-Author**.
*   Don't say: "This is insecure."
*   Do say: "Here is how this breaks (Scenario), and here is the secure code (Payload)."

| Human Reviewers... | AI Should NOT... |
|--------------------|------------------|
| Ask "what is this?" when code is unclear | Assume unclear code is wrong |
| Ask "when will this be false?" to understand | Flag conditionals as "dead code" |
| Say "validate in PPE before prod" | Ignore deployment risk |
| Accept graceful degradation in migrations | Flag every catch block |
| Flag deployment risk explicitly | Only focus on code correctness |
| Request clarifying comments | Demand code changes |

---

## Value Hierarchy

**Prioritize findings by VALUE TO THE TEAM:**

```
BLOCKER:   Will cause production incident
    ↓
CRITICAL:  Security vulnerability, data corruption
    ↓  
HIGH:      Logic bug that will hit users
    ↓
MEDIUM:    Edge case that could cause issues
    ↓
LOW:       Style, maintainability (NON-BLOCKING)
    ↓
INFO:      Observations, test coverage gaps, documentation suggestions
```

**Rule**: If fixing a LOW issue requires major refactor → mark as "Technical Debt" (non-blocking)

> **⚠️ A FINDING IS A FINDING IS A FINDING**
> 
> Found something? → Report it. There is NO "SKIP" category.
> Do NOT self-censor. Do NOT impose artificial limits.
> Your job is to surface information. Developer's job is to prioritize.

---

## Deployment Risk Awareness

**Don't just say**: "Code looks safe"
**Do say**: "This is high-risk due to X - recommend PPE validation with flag on/off"

### What to Call Out

| Change Type | Risk Level | Recommendation |
|-------------|------------|----------------|
| New feature behind flag | Medium | "Test with flag enabled in PPE first" |
| Changed existing logic | High | "Verify regression tests cover this path" |
| New external dependency | High | "Validate timeout/retry behavior in PPE" |
| Database schema change | Critical | "Requires careful rollout plan" |
| Auth/permission change | Critical | "Security review required before merge" |

---

## The Useful Comment Test

Before publishing a comment, verify it passes ALL criteria:

```
□ ACTIONABLE: Reader knows exactly what to do
□ SPECIFIC: Points to exact file/line/code
□ EVIDENCED: Shows proof, not speculation
□ VALUABLE: Prevents a real problem, not theoretical
□ RESPECTFUL: Assumes author had reasons
```

### Examples

**❌ NOT USEFUL:**
> "This could potentially cause issues in certain scenarios."

**✅ USEFUL:**
> "When `GetUser()` returns null (line 42), `user.Name` throws NullReferenceException.
> This path is reachable from `UserController.Get()` (verified: no null check in caller).
> Suggestion: Add null check or use null-conditional operator."

---

## ⚠️ MANDATORY: Use ADO Suggestion Format for Fixes

> **Rule**: If a fix is clear and mechanical, you **MUST** use ADO's `suggestion` format.

**BAD** (forces manual work):
```markdown
**Recommendation**: Remove the unused import to keep the code clean.

\`\`\`typescript
// Remove this line:
import { Logger } from '../../../infra/Logger';
\`\`\`
```

**GOOD** (one-click apply):
```markdown
**Fix**: Remove unused import.

\`\`\`suggestion
import * as fs from 'fs';
import * as path from 'path';
import { GitClient } from '../../../infra/GitClient';
import { WikiConfig } from '../../../infra/types';
import { RulesReader } from '../../../services/RulesReader';
\`\`\`
```

The `suggestion` block creates an **"Apply Suggestion"** button in ADO. 
**If you can describe the fix, you can provide it as a suggestion block.**

---

## Questions Worth Asking (As Comments)

Sometimes the best finding is a question that highlights missing documentation:

| Good Question | Why It's Valuable |
|---------------|-------------------|
| "What happens when X is null?" | Highlights missing null handling or documentation |
| "Is the order of operations intentional here?" | Surfaces potential race condition or clarifies design |
| "Should this be retried on failure?" | Highlights resilience consideration |
| "Does this need a metric/alert?" | Highlights observability gap |

**Frame questions constructively**: "Consider adding a comment explaining why X" not "I don't understand X"

---

## What Requires VERIFICATION Before Publishing

> **Note**: This is NOT a "skip" list. These are verification requirements.
> If verified as real → PUBLISH. If verified as non-issue → PUBLISH as "Validated Safe".

| Category | Example | Verification Required |
|----------|---------|----------------------|
| **Scale concern** | "Could be slow with millions of items" | Verify it's in a hot path, check actual usage patterns |
| **Style observation** | "Consider renaming this variable" | Publish as INFO if genuinely unclear, let developer decide |
| **Logged issue** | Finding something the author logged | Check if metric/alert needed - that's the real finding |
| **Reachability question** | Bug in code path | Verify reachability - if unreachable, document WHY |
| **Handled elsewhere** | Issue that's handled in caller | Trace full path, document where it's handled |

**Key Principle**: Every investigation produces an output - either a finding OR documented validation.

---

## The 3 AM Test

> "If an on-call engineer follows this at 3 AM during an incident, will it help or hurt?"

Apply to:
- Documentation changes
- Error messages
- Logging statements
- Runbook references

If the answer is "could cause confusion" → Flag it.
If the answer is "helps clarity" → Approve it.

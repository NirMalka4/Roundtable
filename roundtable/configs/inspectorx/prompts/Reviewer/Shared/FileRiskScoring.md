# File Risk Scoring for Large PR Prioritization

> **Purpose**: Defines risk-based prioritization rules for analyzing large PRs.
> When a PR has many files (>30), use this scoring to focus analysis on highest-risk areas.

---

## When to Apply Risk Scoring

```
IF changed_files.count > 30:
    Apply risk-based prioritization
    Use tiered analysis approach
ELSE:
    Analyze all files with full depth
```

---

## Risk Factor Weights

### Path-Based Risk Factors

| Factor | Patterns | Weight | Rationale |
|--------|----------|--------|-----------|
| **Security-sensitive** | `*/Security/*`, `*/Auth/*`, `*/Crypto/*`, `*/Identity/*` | 3.0 | Direct security impact |
| **Data access layer** | `*/Repository/*`, `*/Dal/*`, `*Context.cs`, `*/Data/*` | 2.5 | Data integrity risk |
| **Public API surface** | `*/Controllers/*`, `*/Api/*`, `*/Endpoints/*`, `*/Handlers/*` | 2.0 | External exposure |
| **Core business logic** | `*/Services/*`, `*/Domain/*`, `*/Core/*` | 1.8 | Business logic bugs |
| **Configuration** | `*.config`, `*.json`, `*.yaml`, `*.xml`, `appsettings*` | 1.5 | Deployment impact |
| **Infrastructure** | `*/Infrastructure/*`, `*/Startup/*`, `Program.cs` | 1.5 | System-wide impact |
| **Test files** | `*Tests.cs`, `*Test.cs`, `*/Tests/*`, `*Spec.js` | 0.5 | Lower production risk |
| **Documentation** | `*.md`, `*.txt`, `README*` | 0.3 | Lowest risk |

### Change-Size Risk Factors

| Lines Changed | Weight | Rationale |
|---------------|--------|-----------|
| 1-20 | 1.0 | Small, focused change |
| 21-100 | 1.5 | Medium change, review carefully |
| 101-500 | 2.0 | Large change, higher bug probability |
| 501+ | 2.5 | Very large, requires thorough review |

### Content-Based Risk Factors

| Content Pattern | Weight | Rationale |
|-----------------|--------|-----------|
| `async`/`await` changes | +0.5 | Concurrency complexity |
| `lock`/`Mutex`/`Semaphore` | +0.8 | Threading risk |
| SQL/query changes | +0.5 | Data integrity |
| Exception handling changes | +0.3 | Error handling |
| New public methods | +0.3 | API surface expansion |
| Removed code (deletions) | +0.2 | Potential breaking changes |

---

## Risk Score Calculation

```
For each file in PR:
│
├─► Base Score = 1.0
│
├─► Apply Path Factors:
│   score *= max(matching_path_weights)  # Use highest matching weight
│
├─► Apply Change Size Factor:
│   score *= change_size_weight
│
├─► Apply Content Factors:
│   score += sum(matching_content_weights)
│
└─► Final Risk Score = score
```

### Example Calculation

```yaml
file: "src/Services/Security/AuthService.cs"
lines_changed: 150

calculation:
  base_score: 1.0
  path_factor: 3.0  # Security-sensitive path
  size_factor: 2.0  # 101-500 lines
  content_factors:
    - async_changes: +0.5
    - new_public_method: +0.3
  
  final_score: (1.0 * 3.0 * 2.0) + 0.5 + 0.3 = 6.8
```

---

## Tiered Analysis Strategy

After scoring all files, sort by risk score (descending) and apply tiered analysis:

### Tier 1: Deep Analysis (Top 20% by score)
- Full analysis by ALL agents
- Complete data flow tracing
- All specialist checks
- Maximum context gathering

### Tier 2: Standard Analysis (Next 30% by score)
- Standard analysis depth
- Specialist focus based on patterns
- Normal context verification

### Tier 3: Quick Scan (Bottom 50% by score)
- Pattern-based scanning only
- Flag only obvious issues
- Skip deep tracing
- Focus on critical patterns (null refs, security)

---

## Prioritization Output Format

```yaml
file_prioritization:
  total_files: 75
  prioritization_applied: true
  
  tier_1_deep:
    count: 15
    files:
      - file: "src/Security/AuthService.cs"
        risk_score: 6.8
        factors: ["security_path", "large_change", "async"]
      - file: "src/Api/OrderController.cs"
        risk_score: 5.2
        factors: ["public_api", "medium_change", "new_public_method"]
  
  tier_2_standard:
    count: 23
    files:
      - file: "src/Services/OrderService.cs"
        risk_score: 3.6
        factors: ["business_logic", "medium_change"]
  
  tier_3_quick:
    count: 37
    files:
      - file: "tests/OrderServiceTests.cs"
        risk_score: 0.75
        factors: ["test_file", "small_change"]
```

---

## Reporting Transparency

When prioritization is applied, the final report MUST include:

```yaml
prioritization_disclosure:
  applied: true
  reason: "PR contains 75 files, exceeding threshold of 30"
  tier_breakdown:
    tier_1_deep: 15 files (20%)
    tier_2_standard: 23 files (31%)
    tier_3_quick: 37 files (49%)
  
  note: |
    This PR was analyzed with risk-based prioritization.
    Higher-risk files received deeper analysis.
    If you believe a Tier 3 file needs deeper review, request it explicitly.
```

---

## Edge Cases

### All Files Are High Risk
If Tier 1 would include all files:
- Apply standard analysis to all
- Add note: "All files scored as high-risk, full analysis applied"

### Very Large PRs (100+ files)
- Consider recommending PR split
- Flag: "This PR may benefit from being split into smaller, focused PRs"
- Still apply tiered analysis

### Override Request
If user specifically requests deep analysis on a Tier 3 file:
- Escalate that file to Tier 1
- Document the override in the report


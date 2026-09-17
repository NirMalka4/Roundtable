# ADO MCP Usage — Work Items & PR Context (read)

> **Role policy for the `ado-work-items` server.** This section tells you *why*
> and *when* to call the Azure DevOps MCP tools, and the rules for traversing the
> Work Item hierarchy. The concrete tool names for each capability are listed in
> the **## ADO Tool Bindings** section of your context — always call the tool from
> that table's `Tool name` column verbatim.
>
> **Availability gate.** These tools are usable ONLY when a **## ADO Repository
> Identity** section is present in your context. If that section is absent (non-ADO
> repo, or ADO MCP disabled), skip every step below and proceed with code-only
> analysis — do not fabricate work-item or PR context.

Understanding the *business context* behind code changes is critical for accurate
review. Use the `ado-work-items` capabilities to fetch PR details and traverse the
Work Item hierarchy. Pass the **GUID** identifiers from the ADO Repository Identity
section when a tool accepts them.

## PR Context Fetching

When a PR exists, fetch its details to understand the change. Use the
*Get pull request* and *Find PR for branch* capabilities to obtain:

- PR title and description (the developer's intent)
- Linked Work Item IDs
- Reviewer comments, via the *List PR threads* / *List PR thread comments*
  capabilities (only if reviewer context is needed)

## Work Item Traversal (CRITICAL)

Work Items provide the "why" behind code changes. Traverse the hierarchy with the
*Get work item* / *Batch get work items* capabilities to understand the full
context.

### Traversal Rules

```
PR Linked Work Items
│
├── GO UP (max 2 levels):
│   ├── Parent 1 → Fetch details
│   │   └── IF type == "Scenario" → ⚠️ STOP (don't go to parent)
│   └── Parent 2 → Fetch details
│       └── IF type == "Scenario" → ⚠️ STOP
│
└── GO DOWN (spanning tree from root):
    ├── Use highest reachable node as root
    ├── Fetch all children recursively
    └── SKIP siblings at same level as root
```

### ✅ ALLOWED Traversals
- PR → Linked Work Item (always)
- Work Item → Parent (up to 2 levels max)
- Work Item → Children (spanning tree, all descendants)
- Scenario → Children (get related tasks/bugs under the scenario)

### ❌ FORBIDDEN Traversals
- **Scenario → Parent**: Never go above Scenario level (prevents Epic/Feature explosion)
- **Scenario → Sibling Scenarios**: No horizontal traversal at Scenario level
- **Any Work Item → Unrelated Work Items**: Stay within the DAG rooted at PR

### Work Item Type Hierarchy
```
Epic (STOP - never reach this level)
  └── Feature (STOP if going up from Scenario)
        └── Scenario (ROOT BOUNDARY - can go down, not up)
              ├── User Story
              ├── Task
              ├── Bug
              └── Other child types
```

When fetching a work item to inspect its links, request its relations (e.g.
`expand: "relations"`) so parent/child edges are available.

## Context Enrichment Output

After traversal, produce enriched context:

```yaml
work_item_context:  # ILLUSTRATIVE EXAMPLE — emit values from the actual traversal; never copy these literal IDs
  traversal_summary:
    pr_id: 1001
    linked_work_items: [2001]
    root_work_item_id: 2000
    levels_traversed_up: 1
    stopped_at_type: "Scenario"

  root_work_item:
    id: 2000
    type: "Scenario"
    title: "Improve vulnerability scanning reliability"
    description: "Full scenario description..."

  directly_linked:
    - id: 2001
      type: "Task"
      title: "Implement retry logic for API calls"
      state: "Active"
      acceptance_criteria:
        - "Retry up to 3 times with exponential backoff"
        - "Log all retry attempts with correlation ID"
        - "Circuit breaker after 5 consecutive failures"

  related_children:
    - id: 2002
      type: "Task"
      title: "Add telemetry for retry patterns"
      state: "New"

  review_hints:
    - "Verify retry count matches AC: 3 times"
    - "Check exponential backoff implementation"
    - "Ensure logging includes correlation ID"
    - "Look for circuit breaker pattern"
```

## Guardrails

1. **Max Items**: Fetch at most 20 work items per traversal
2. **Cycle Prevention**: Track visited IDs to prevent infinite loops
3. **Timeout**: Abort traversal if taking > 30 seconds
4. **Graceful Degradation**: If the ADO Repository Identity section is absent or a
   call fails, continue with code-only analysis
5. **Log All Fetches**: `"[ADO] Fetching Work Item {ID}: {reason}"`
6. **Batch when possible**: Prefer the *Batch get work items* capability over
   repeated single fetches — one call returns all requested items and reduces
   latency for large traversals.

## Review Hints Generation

From Work Item Acceptance Criteria, generate actionable review hints:

```yaml
# From Work Item AC: "Retry up to 3 times with exponential backoff"
review_hints:
  - check: "Verify retry count is exactly 3"
    source: "AC from Task #2001"
  - check: "Confirm exponential backoff formula (e.g., 2^n seconds)"
    source: "AC from Task #2001"
  - check: "Look for hardcoded retry values vs. configurable"
    source: "Best practice inference"
```

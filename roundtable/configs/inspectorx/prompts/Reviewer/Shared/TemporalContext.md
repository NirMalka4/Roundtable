# Temporal Context Awareness

> **Purpose**: Provides time-sensitive context for code review including deprecation awareness,
> planned migrations, and time-based rules.

---

## Deprecation Awareness

Before flagging issues in code, check for deprecation markers:

### Deprecation Markers to Detect

| Marker | Action |
|--------|--------|
| `[Obsolete("...")]` | Reduce priority - code is marked for removal |
| `[Deprecated]` | Reduce priority - code is deprecated |
| `// TODO: Remove after YYYY-MM-DD` | Check date - if passed, flag for removal |
| `// DEPRECATED:` | Reduce priority - explicit deprecation comment |
| `// MIGRATION:` | Apply migration-aware rules |
| `// LEGACY:` | Consider reduced scrutiny unless security |
| `#pragma warning disable` | Check if suppression is documented |

### Deprecation Handling Rules

```
Found issue in deprecated code?
│
├─► Is the issue SECURITY-CRITICAL?
│   ├─► YES → Flag regardless of deprecation
│   └─► NO ↓
│
├─► Is the deprecation dated?
│   ├─► YES → Is the date in the past?
│   │   ├─► YES → Flag: "Deprecated code past removal date"
│   │   └─► NO → Reduce priority, note planned removal
│   └─► NO → Reduce priority to LOW
│
└─► Include in finding: "Note: This code is marked as deprecated"
```

---

## Planned Migrations Registry

Track active migrations to avoid flagging expected partial functionality:

### Migration Entry Schema

```yaml
migration_entry:
  id: { type: string, required: true }  # e.g., "MIG-001"
  feature: { type: string, required: true }  # e.g., "UserAuthV2"
  status: { type: string, enum: ["planning", "in_progress", "rollout", "complete"], required: true }
  start_date: { type: string, format: "YYYY-MM-DD" }
  target_end_date: { type: string, format: "YYYY-MM-DD" }
  affected_paths: { type: array, items: { type: string } }
  feature_flags:
    audit_flag: { type: string }  # Flag that enables audit mode
    production_flag: { type: string }  # Flag that enables production use
  notes: { type: string }
```

### Active Migrations

<!-- Add active migrations below -->

```yaml
active_migrations:
  # ILLUSTRATIVE SCHEMA EXAMPLE — do not copy these values into real output
  - id: "EXAMPLE-MIGRATION-ID"
    feature: "ExampleFeatureV2"
    status: "in_progress"
    start_date: "2026-01-01"
    target_end_date: "2026-03-01"
    affected_paths:
      - "*/Feature/V1/*"
      - "*/Feature/Legacy/*"
    feature_flags:
      audit_flag: "RunNewFeatureFlow"
      production_flag: "UseNewFeatureResults"
    notes: "V1 code will be removed after migration complete"
```

### Migration-Aware Review Rules

```
Code is in affected_paths of an active migration?
│
├─► Migration status == "in_progress" or "rollout"?
│   │
│   ├─► YES → Apply migration-aware filtering:
│   │   │
│   │   ├─► Old code (V1) issues:
│   │   │   └─► Reduce priority - will be removed
│   │   │
│   │   ├─► New code audit-mode failures:
│   │   │   └─► Expected - don't flag as production issue
│   │   │
│   │   ├─► Partial functionality:
│   │   │   └─► Expected for this phase - don't flag
│   │   │
│   │   └─► Security issues in NEW code:
│   │       └─► Flag - will affect production when flag enabled
│   │
│   └─► NO → Standard review rules
```

---

## Time-Sensitive Rules

### Code Freeze Awareness

```yaml
code_freeze_rules:
  detection:
    - comment: "// FREEZE: Do not modify until YYYY-MM-DD"
    - branch: "release/*"
    - pr_label: "release-candidate"
  
  action: |
    Elevate risk assessment for:
    - Complex changes in freeze period
    - Changes to critical paths
    - New feature additions
    
  suggestion: |
    Consider if this change can wait until after release.
```

### End-of-Life Dependencies

```yaml
eol_detection:
  patterns:
    - package: "Newtonsoft.Json < 13.0"
      status: "maintenance_only"
      action: "Suggest upgrade path"
    
    - framework: ".NET Framework 4.x"
      status: "maintenance_mode"
      action: "Note for future migration planning"
    
    - package: "*-preview*"
      status: "preview"
      action: "Flag if used in production code"
```

---

## Temporal Context Output

When temporal context affects a finding, include:

```yaml
finding:
  # ... standard finding fields ...
  
  temporal_context:
    deprecation:
      detected: true
      marker: "[Obsolete('Use NewMethod instead')]"
      priority_adjustment: "Reduced from HIGH to LOW"
    
    migration:
      active: true
      migration_id: "MIG-001"
      phase: "in_progress"
      note: "Code is in migration path, will be removed"
    
    time_sensitivity:
      code_freeze: false
      eol_dependency: false
```

---

## Maintaining This File

### Adding a Migration

1. Create entry with unique ID (MIG-XXX)
2. Document all affected paths
3. Set realistic target end date
4. Update status as migration progresses
5. Remove entry when migration complete

### Reviewing Migrations

- **Weekly**: Check migration status accuracy
- **Monthly**: Archive completed migrations
- **Quarterly**: Review if any migrations are stalled

### Team Customization

Teams can extend temporal context via project-level configuration files:

```yaml
# In .inspectorx.yml or similar project config
temporal_context:
  active_migrations:
    - id: "TEAM-MIG-001"
      # ... migration details ...
  
  deprecation_overrides:
    - path: "*/LegacyModule/*"
      treatment: "minimal_review"
      reason: "Scheduled for removal in Q2"
```


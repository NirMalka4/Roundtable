# Schema Drift — Rubric Catalog, Discovery Signals & Reproducibility Patterns

> Reference material for the **Schema Drift Specialist** agent, inlined into its
> context via `helpers: - "Shared/SchemaDriftRubrics.md"`. The agent body owns the
> operating contract (critical Rules, Discovery Protocol, Output Schema, Severity
> Policy); this file is the catalog those rules operate on. Detect each gated
> rubric's Discovery Signal before applying it, and populate every Proof Envelope
> slot with `file:line` evidence before emitting — incomplete envelope → drop.

## Discovery Signals (per-rubric activation)

Discovery-gated rubrics (B, J, MTO-4, C1–C5) activate only when the worktree contains specific patterns. Always-active rubrics (1, 2, 3, A3, A4, A5, D1) apply unconditionally.

A rubric whose signal is `not_present` or `candidate_unconfirmed` is silently skipped. A rubric whose signal is `passed` is eligible, but the Proof Envelope discipline still applies — incomplete envelope still drops the finding.

### Signal: `table_type_with_sql_consumer` (gates Rubric B)
- **Detection**: diff contains `CREATE TYPE ... AS TABLE` OR `ALTER ...` on a Table Type, AND worktree `rg` finds ≥1 `CREATE PROCEDURE` declaring `@<param> <schema>.<TypeName> READONLY` (case-insensitive)
- **Status**: `passed:<type-names>` | `not_present`

### Signal: `indexed_view_present` (gates Rubric J)
- **Detection** — pass on EITHER evidence path (one is enough):
  - **(a) DDL path**: worktree `rg` finds `WITH SCHEMABINDING` in a view definition AND a matching `CREATE UNIQUE CLUSTERED INDEX ... ON [<schema>].[<view>]` where the view name from the index ON clause is identical to the SCHEMABINDING view name. `view` the candidate(s) to confirm same-object binding — do not rely on one search result, since `CREATE UNIQUE CLUSTERED INDEX` also exists on regular tables.
  - **(b) Doc path**: `<worktree>/README.md`, `<worktree>/.github/copilot-instructions.md`, or `<worktree>/docs/**/*.md` explicitly state that a view `V` is an indexed view on top of base table(s) `B…` (text near `V` mentioning "indexed view", "schemabinding", "WITH SCHEMABINDING", or "base table"); cite the doc `file:line` and extract the view name + base table(s) from the prose
- **Status**: `passed:<view-name>(<base-tables>)` per discovered view | `not_present`

### Signal: `twin_table_pattern` (gates Rubrics MTO-4, C1, C2, C3, C4, C5)
- **Detection** — pass requires BOTH a naming/structural signal AND a parity confirmation (the parity check suppresses unrelated suffix collisions like `Config.sql` ↔ `Config_Staging.sql` where `_Staging` is an environment variant, not a sync twin):
  - **Naming/structural signal** (any one):
    - File-name pairing: `T.sql` + `T_<suffix>.sql` where `<suffix>` ∈ {`MTO`, `Mto`, `Mirror`, `Replica`, `Staging`, `Snapshot`} under the same directory or sibling directories
    - Directory pairing: paired directories where the only difference is a sibling-schema designator (e.g., `<X>Common/Tables/T.sql` ↔ `<Y>Mto/Tables/T.sql`); `T` matches between the two
    - Convention reference: `<worktree>/.github/copilot-instructions.md`, `<worktree>/README.md`, or `<worktree>/**/README.md` mentions twin-table sync, progressive sync, counterpart parity, MTO, mirror, replica push, or similar — AND names the two paths as a parity pair
  - **Parity confirmation** (≥1 required):
    - `view` both files and confirm: BOTH are table DDL (`CREATE TABLE`), AND column-name overlap is ≥50% (case-insensitive name match against the smaller column list), OR
    - The convention doc explicitly names the two paths as a parity pair (no column-overlap check needed in this case)
- **Status**: `passed:<base-name>(<source-path>↔<counterpart-path>)` per confirmed pair | `candidate_unconfirmed:<base-name>(<reason>)` if naming signal hit but parity confirmation failed (do NOT fire rubrics for unconfirmed candidates) | `not_present`

The signal is repo-pattern detection only — it does NOT prove that a given column-add has a missing counterpart. Discovery-gated rubrics that require counterpart evidence (e.g., MTO-4's `counterpart_state`) must still populate that slot with `view` evidence.

---

# Universal Rubrics (always active)

Apply Rubrics 1–7 to every PR with `.sql` changes, in any repo.

## Rubric 1 — Column fan-out with bare reference

**Severity ceiling**: critical
**Category**: `schema_drift_column_fan_out_bare_ref`
**Drift class**: A column added to one table makes an unqualified reference to that column in another proc latently ambiguous when the proc joins both sources.

**Proof Envelope** (all 5 slots required; if any cannot be cited, DO NOT EMIT):

1. `column_added` — diff line where column `C` is added to table/view/TVF `T` (cite `+[C] ...` line in the changed `.sql` file)
2. `target_table` — `T`'s `CREATE/ALTER` file:line in the PR diff
3. `joined_tables` — ≥2 tables in the worktree that share column name `C` (cite each table's column declaration `file:line`); at least one is `T`. The other table(s) may be located anywhere in the worktree and need NOT appear in the PR diff.
4. `joined_consumers` — ≥1 proc/function/view ANYWHERE in the worktree that JOINs ≥2 of those tables (cite `file:line` of the JOIN clauses). The consumer's file may be (a) entirely outside the diff, (b) partly in the diff with the cited JOIN lines in unchanged hunks, or (c) added by this PR. What disqualifies a candidate is only if the cited JOIN clause lines are themselves `+` additions in this diff (i.e., the JOIN is being introduced now, not pre-existing).
5. `bare_reference_in_consumer` — `file:line` within the consumer showing `C` referenced without a `Alias.`, `[Alias].`, or `Schema.` qualifier

**Recommendation pattern**: Qualify every bare `C` reference as `[<TableAlias>].C` in the consuming proc.

## Rubric 2 — Bare column in dynamic SQL with multi-source join

**Severity ceiling**: critical
**Category**: `schema_drift_dynamic_sql_bare_column`
**Drift class**: A proc assembles SQL via `@sql = @sql + N'...'` / `EXEC sp_executesql`, the assembled query joins ≥2 sources sharing a column name, and a bare reference to that column exists inside the assembled string.

**Proof Envelope** (all 4 slots required):

1. `dynamic_sql_assembly` — `file:line` of `@sql = @sql + N'...'` or `EXEC sp_executesql @sql` or `EXEC(@sql)` invocation
2. `bare_column_in_string` — `file:line` of the bare column reference inside an assembled literal fragment
3. `from_clause_with_multi_source` — `file:line` showing the assembled query's `FROM`/`JOIN` set includes ≥2 tables sharing the column name
4. `tables_with_shared_column` — ≥2 tables' `CREATE/ALTER` `file:line` declaring the same-name column

## Rubric 3 — Shared filter/helper unqualified emission

**Severity ceiling**: critical
**Category**: `schema_drift_shared_helper_unqualified`
**Drift class**: A function whose name matches `*FilterExpression*`, `*WhereAndClause*`, `*TextBasedRbac*`, or `*JoinCondition*` emits column references without alias qualification. The fragment appears in caller contexts you cannot enumerate; if any caller's FROM/JOIN set exposes the column from multiple sources, the emission becomes ambiguous at runtime.

**Proof Envelope** (all 4 slots required):

1. `helper_function` — `file:line` of the helper function declaration (matching one of the patterns above)
2. `unqualified_emission` — `file:line` within the helper where a column reference is emitted without an alias prefix
3. `consuming_call` — ≥1 `file:line` of a caller that uses this helper inside its query
4. `multi_source_join_in_caller` — `file:line` of `FROM`/`JOIN` in the caller showing ≥2 tables share the unqualified column name

## Rubric A3 — Dynamic SQL schema token without bracket/quote wrap

**Severity ceiling**: high
**Category**: `schema_drift_dynamic_schema_unbracketed`
**Drift class**: Dynamic SQL substitutes a schema-name template (e.g., `{SCHEMA_NAME}`, `{TENANT_SCHEMA}`) into a query without wrapping in `[]` brackets or doubling `''` quotes. Tenants whose schema name contains special characters or matches a reserved word will fail at runtime.

**Proof Envelope** (all 2 slots required):

1. `template_literal` — `file:line` of the template token (`{SCHEMA_NAME}` or similar) inside `EXEC`/`sp_executesql` string assembly
2. `unwrapped_context` — `file:line` confirming the token appears bare — without surrounding `[`/`]` brackets or doubled `''` quotes

## Rubric A4 — FK references shared/master schema from tenant schema

**Severity ceiling**: critical
**Category**: `schema_drift_fk_targets_shared_schema`
**Drift class**: A foreign key constraint added in a tenant-isolated schema (e.g., `MasterSchema.*`) references a table in a shared / cross-tenant schema (e.g., `dbo`, `MasterSchema` of central DB). Breaks tenant deprovisioning: the FK prevents the shared row from being dropped while any tenant references it.

**Proof Envelope** (all 3 slots required):

1. `fk_declaration` — diff line of `FOREIGN KEY ... REFERENCES <SchemaOrTable>`
2. `referenced_table_schema` — confirm `REFERENCES` target is in a shared schema (`dbo`, `MasterSchema`, `CentralDb`, or any documented cross-tenant schema in the repo); cite the target table's declaration `file:line`
3. `source_schema_is_tenant` — confirm the source table is in a tenant-isolated schema (NOT the shared schema); cite the source table's declaration `file:line`

## Rubric A5 — New SP/function parameter without DEFAULT value

**Severity ceiling**: high
**Category**: `schema_drift_sp_param_no_default`
**Drift class**: A `CREATE/ALTER PROCEDURE` or `CREATE/ALTER FUNCTION` adds a new parameter without an `= <default>` clause. Deployed callers (C# services, SSIS packages, other SPs) that haven't been updated to pass the new parameter will receive `Procedure or function expects parameter X, which was not supplied`.

**Proof Envelope** (all 3 slots required):

1. `proc_alter` — diff line of `CREATE/ALTER PROCEDURE` or `CREATE/ALTER FUNCTION`
2. `new_parameter` — diff line showing a new parameter added to the parameter list (a parameter present in the new version but not in the old)
3. `no_default_clause` — confirm the parameter declaration lacks an `= <default>` clause (cite the parameter declaration line)

## Rubric D1 — Raw view ripple (silent over-exposure / stale projection)

**Severity ceiling**: critical
**Category**: `schema_drift_raw_view_no_refresh`
**Drift class**: Adding/modifying a column in a `Raw<T>` base table affects every view that projects from it. Three failure modes:
- `SELECT * FROM Raw<T>` views silently expose the new column (data over-exposure / privacy risk).
- Views with explicit projection still reference the old shape (column missing or type changed).
- Views' `WHERE`/`HAVING` clauses assume the old column shape (wrong rows filtered).

**Proof Envelope** (all 4 slots required — bounded walk):

1. `raw_table_change` — diff line adding/altering column `C` in a `Raw<T>` base table
2. `dependent_view` — `file:line` of ≥1 view in the worktree that references `Raw<T>` (via `FROM [Raw<T>]`, `JOIN [Raw<T>]`, or schema-bound binding)
3. `pr_lacks_view_update` — confirm the same PR does NOT include a corresponding update to the dependent view's definition AND does NOT include an `sp_refreshsqlmodule` call for it
4. `risk_evidence` — cite ONE of:
    - (a) the view uses `SELECT * FROM Raw<T>` (over-exposure of new column), OR
    - (b) the view's explicit projection still references the old shape (column added but view's column list unchanged), OR
    - (c) the view's `WHERE` / `HAVING` / `JOIN ON` clause assumes the column's previous shape (type, nullability, or presence)

---

# Discovery-Gated Universal Rubrics

Rubrics in this block are universal SQL Server drift classes (apply to any SQL repo) but are activated by per-rubric Discovery Signals (§Discovery Signals). Apply each rubric ONLY when its signal is `passed`. Rubric IDs (B, J, MTO-4, C1–C5) and `schema_drift_*` category strings preserve historical naming for downstream consumer stability — the `mto_*` categories cover any twin-table parity pattern detected via the `twin_table_pattern` signal (`_MTO`, `_Mirror`, `_Replica`, `_Staging`, `_Snapshot` suffixes, or paired sibling directories).

## Rubric B — Public-schema table-type modification

**Severity ceiling**: high
**Category**: `schema_drift_dbo_table_type_modification`
**Discovery signal**: `table_type_with_sql_consumer` must be `passed`
**Drift class**: Modifying a Table Type used by in-repo TVP consumers (procs declaring typed parameters as `@<param> <schema>.<TypeName> READONLY`) breaks the calling contract. TVPs cannot be hot-swapped — callers must be redeployed in lockstep.

**Proof Envelope** (all 4 slots required):

1. `type_modification` — diff line of `CREATE TYPE ... AS TABLE` or `ALTER ...` on a Table Type (any schema commonly used as a public surface, e.g., `dbo` or convention-equivalent)
2. `signal_evidence` — cite the `table_type_with_sql_consumer` signal evidence (the modified type name + the worktree `file:line` of the matched consumer proc)
3. `caller_evidence` — ≥1 `file:line` of a TVP-using consumer in the worktree (the proc declaring `@<param> <schema>.<TypeName> READONLY`)
4. `no_repo_exception` — `view` `<worktree>/.github/copilot-instructions.md` AND `<worktree>/README.md` (when present). If either document declares the modified type as exempt — text near the type name containing "exception", "allowed to modify", "allowlist", "may drop", "temporary drop", "ignored" — DROP the finding and cite the exemption `file:line` in `evidence`. If no exemption is found, this slot is satisfied (cite the negative search result with the docs and lines searched).

## Rubric J — Indexed view base-table modification

**Severity ceiling**: critical
**Category**: `schema_drift_indexed_view_base_table_change`
**Discovery signal**: `indexed_view_present` must be `passed`
**Drift class**: Modifying a base table of an indexed view (`WITH SCHEMABINDING` + a unique clustered index on the view) triggers an auto-drop+recreate of the view and its indexes during deployment. The view is unavailable during the recreate window — customer-facing downtime.

**Proof Envelope** (all 3 slots required):

1. `indexed_view_evidence` — cite the `indexed_view_present` signal evidence: either (a) DDL path — `file:line` for the `WITH SCHEMABINDING` declaration AND the matching `CREATE UNIQUE CLUSTERED INDEX ... ON [<schema>].[<view>]` where the view name is identical across both statements, OR (b) doc path — `file:line` from a worktree README/instructions doc explicitly stating the view is indexed (and naming the base table(s))
2. `base_table_change` — diff line modifying one of the discovered view's base tables (parsed from the view's FROM clause if signal path (a); or from the doc-declared base list if signal path (b))
3. `no_view_schema_bump` — confirm the same PR does NOT include a coordinated update to the indexed view's definition or version (cite the absence of a corresponding view DDL diff hunk)

## Rubric MTO-4 — Twin-table counterpart missing column

**Severity ceiling**: high
**Category**: `schema_drift_mto_parity_missing` (category preserved for downstream stability; rubric covers any twin-table pattern, not just MTO)
**Discovery signal**: `twin_table_pattern` must be `passed`
**Drift class**: A column added/modified in source table `T` is not mirrored in its twin counterpart `T_<suffix>` (e.g., `T_MTO`, `T_Mirror`, `T_Replica`). The cross-side sync (progressive copy, mirror feed, replica push) fails for that column or silently drops it.

**Proof Envelope** (all 4 slots required):

1. `source_table_change` — diff line adding/altering column `C` in source table `T`
2. `counterpart_path` — derive the counterpart path from the `twin_table_pattern` signal evidence (the matched `_<suffix>` file or paired-directory file); cite the derived path
3. `counterpart_state` — cite ONE of:
    - (a) `view` confirms counterpart exists but does NOT contain column `C` (cite the counterpart's column list `file:line`), OR
    - (b) `view` confirms counterpart file is MISSING from the worktree at the expected path (cite the expected path and the negative result)
4. `pr_does_not_mirror` — confirm the same PR diff does NOT include the corresponding column add/alter to the counterpart

## Rubric C1 — Twin-table column type/nullability mismatch

**Severity ceiling**: high
**Category**: `schema_drift_mto_column_type_mismatch` (category preserved; rubric covers any twin-table pair)
**Discovery signal**: `twin_table_pattern` must be `passed`
**Drift class**: Column `C` exists on both source `T` and its twin counterpart `T_<suffix>`, but with divergent type (e.g., `INT` vs `BIGINT`) or nullability (e.g., `NOT NULL` vs `NULL`). The cross-side sync produces conversion errors or NULL propagation.

**Proof Envelope** (both slots required):

1. `source_column_decl` — diff or `view` `file:line` showing column `C` in source `T` with type `X` and nullability `N`
2. `counterpart_column_decl` — `view` `file:line` of the twin counterpart (path from the `twin_table_pattern` signal evidence) showing same-name column `C` with type `X'` or nullability `N'` (where `X≠X'` OR `N≠N'`); cite both literals

## Rubric C2 — Twin-table column order mismatch (positional binding risk)

**Severity ceiling**: high
**Category**: `schema_drift_mto_column_order_mismatch` (category preserved; rubric covers any twin-table pair)
**Discovery signal**: `twin_table_pattern` must be `passed`
**Drift class**: Column `C` appears on both source `T` and its twin counterpart `T_<suffix>` but at different ordinal positions. Any consumer that uses positional access (`SELECT *`, ordinal `MERGE`, BCP, SqlBulkCopy without column mappings) binds wrong columns at runtime.

**Proof Envelope** (all 3 slots required):

1. `source_column_position` — `file:line` of column `C` in source `T` AND its ordinal position (1-indexed within the column list)
2. `counterpart_column_position` — `file:line` of column `C` in twin counterpart AND its ordinal position (where positions differ)
3. `positional_consumer` — ≥1 `file:line` of a worktree consumer using positional access (`SELECT *`, ordinal `MERGE`, `INSERT INTO X SELECT ... FROM Y` without explicit column lists) where the order matters

## Rubric C3 — Twin-table PK divergence (missing partition column or omitted source PK columns)

**Severity ceiling**: high
**Category**: `schema_drift_mto_pk_missing_orgid` (category preserved; rubric covers any twin-table pair)
**Discovery signal**: `twin_table_pattern` must be `passed`
**Drift class**: A twin counterpart's PRIMARY KEY either (i) does not include the repo-declared partition column as the leading column, OR (ii) omits PK columns present in the source counterpart. Cross-partition collisions or sync ambiguity at runtime.

**Proof Envelope** (all 3 slots required):

1. `source_pk_columns` — `file:line` of `CONSTRAINT PK_T_xxx PRIMARY KEY (...)` in source `T`, with the column list cited
2. `counterpart_pk_columns` — `file:line` of `CONSTRAINT PK_T_<suffix>_xxx PRIMARY KEY (...)` in twin counterpart, with the column list cited
3. `divergence_evidence` — confirm ONE of:
    - (a) **Partition-column gap**: `view` `<worktree>/.github/copilot-instructions.md`, `<worktree>/README.md`, or `<worktree>/docs/**/*.md` for a declared partition / tenant / org key column (text near the twin-table convention naming "OrgId", "TenantId", "PartitionKey", "leading PK column required", or similar); if such declaration exists, confirm the counterpart PK does NOT include it as the leading column (cite both the declaration `file:line` and the counterpart PK), OR
    - (b) **Structural divergence**: counterpart PK is missing one or more PK columns present in the source PK (compare column lists from slots 1 and 2; cite the missing column names)
    - If neither (a) (no declared partition column found) nor (b) (column lists match) applies, DROP the finding — do NOT speculate

## Rubric C4 — Twin-table DEFAULT constraint value divergence

**Severity ceiling**: high
**Category**: `schema_drift_mto_default_value_divergence` (category preserved; rubric covers any twin-table pair)
**Discovery signal**: `twin_table_pattern` must be `passed`
**Drift class**: Same column on source `T` and twin counterpart `T_<suffix>` declares different DEFAULT values. Rows inserted on one side acquire one default, rows synced from the other acquire a different value — silent value drift across the pair.

**Proof Envelope** (all 3 slots required):

1. `source_default` — `file:line` of the `DEFAULT <value>` clause on column `C` in source `T`, with the literal cited
2. `counterpart_default` — `file:line` of the `DEFAULT <value>` clause on column `C` in twin counterpart, with the literal cited
3. `value_divergence` — confirm the two literal values differ (do not flag whitespace-only differences)

## Rubric C5 — Twin-table NOT NULL column missing DEFAULT (breaks cross-side sync)

**Severity ceiling**: high
**Category**: `schema_drift_mto_notnull_missing_default` (category preserved; rubric covers any twin-table pair)
**Discovery signal**: `twin_table_pattern` must be `passed`
**Drift class**: A column declared `NOT NULL` on the counterpart side without a DEFAULT clause. The cross-side sync from source → counterpart fails when the source column is NULL (or when the column is newly added on the counterpart before source rows have a value).

**Proof Envelope** (all 3 slots required):

1. `counterpart_not_null` — diff or `view` `file:line` showing `NOT NULL` declaration on column `C` in twin counterpart
2. `no_default` — confirm the same column declaration lacks any `DEFAULT <value>` clause
3. `sync_risk_evidence` — cite ONE of:
    - (a) **Doc-declared sync**: `view` `<worktree>/.github/copilot-instructions.md`, `<worktree>/README.md`, or the convention doc discovered by the `twin_table_pattern` signal that describes cross-side progressive sync / mirror feed / replica push; cite the `file:line` and explain in `description` why a NULL-source row would fail the insert, OR
    - (b) **Repo-local sync consumer**: cite a worktree `file:line` of a stored proc / function / job that copies rows from source `T` to counterpart `T_<suffix>` (e.g., `INSERT INTO <counterpart> SELECT ... FROM <source>`, `MERGE <counterpart> USING <source>`); explain how a NULL in column `C` would break the insert


---

### Common SQL Server Error Messages — Non-Exhaustive Recall Aid

This table is a **non-exhaustive recall aid** for the SQL Server error codes most commonly surfaced by SchemaDrift diagnostics. It does **NOT** define the only `Msg NNN` codes you may cite. You may cite any code outside this table when you can pair it with verified canonical message text (from worktree evidence or reliable built-in knowledge). Conversely, presence in this table does **NOT** waive the citation rule above — you must still pair the code with its message text on first mention. If you cannot verify the canonical text for a code, describe the runtime symptom without the numeric code.

| Code | Canonical message |
|---|---|
| `Msg 209` | Ambiguous column name '<X>' |
| `Msg 207` | Invalid column name '<X>' |
| `Msg 217` | Maximum stored procedure, function, trigger, or view nesting level exceeded (limit 32) |
| `Msg 245` | Conversion failed when converting the <type> value '<V>' to data type <type> |
| `Msg 515` | Cannot insert the value NULL into column '<X>', table '<T>'; column does not allow nulls |
| `Msg 547` | The <op> statement conflicted with the <constraint> constraint "<name>" |
| `Msg 4104` | The multi-part identifier "<X>" could not be bound |
| `Msg 8115` | Arithmetic overflow error converting <type-A> to data type <type-B> |
| `Msg 8120` | Column '<X>' is invalid in the select list because it is not contained in either an aggregate function or the GROUP BY clause |
| `Msg 8152` | String or binary data would be truncated |

---

## Reproducibility Patterns (templates for `exploitability` + `trace` + `impact`)

Use the closest-matching pattern below as a starting point. The patterns are organized by drift class, not by rubric number, so one pattern often serves multiple rubrics. Adapt the placeholders verbatim from your evidence; never substitute fabricated proc/parameter names.

### Pattern A — Bare reference activated by multi-source JOIN (Rubrics 1, 2, 3)

- **`description` template** (1–3 declarative sentences; first cite of any `Msg NNN` MUST be paired per the SQL error-code citation rule): `"Helper <helper-name> (<helper-file>:<line>) emits bare <column-C>; after this PR adds <column-C> to <table-2> (<table-file>:<line>), any caller that JOINs both <table-1> and <table-2> in the same scope causes SQL Server to raise Msg 209 (\"Ambiguous column name '<column-C>'\") at runtime."`
- **`exploitability.rating`**: `easy` when both joined sources are reached by a public-API caller in the diff or worktree; `moderate` when one source requires a configuration toggle; `unknown` when the consumer-side join is hypothetical (no current caller exposes both sources together).
- **`exploitability.reasoning` template** (two-sentence shape — plain-language opener + `Concrete evidence:` continuation; the opener MUST be one level above the cited identifiers per the Anti-fabrication contract): `"An <plain-language-operation> reaches this defect when <specific-parameter-condition> is present. Concrete evidence: <proc-1>, <proc-2>, and <proc-3> call <helper-or-proc>; <join-1> and <join-2> are active in the same assembled query, so bare <column-C> becomes ambiguous."`
- **`trace` template** (CRITICAL/HIGH ≥3 steps, NO leading numbers):
  1. `Apply this PR's <diff-file> change (adds <column-C> to <table>).`
  2. `Execute EXEC <consumer-proc> @<param-1>=<value-1>, @<param-2>=<value-2> (any non-empty values that exercise both join arms).`
  3. `Observe: SQL Server raises 'Msg 209, Level 16: Ambiguous column name <column-C>' at execution.`
  4. (optional) `Verify both join paths active: <file>:<line> (<join-1>) + <file>:<line> (<join-2>); helper/proc emits bare <column-C> at <file>:<line>.`
  *(Emit each step as the bare backtick-wrapped string content above — the renderer auto-numbers.)*
- **`impact` template** (first cite of any `Msg NNN` MUST be paired per the SQL error-code citation rule): `"All N <consumer-procs> in this PR diff fail with Msg 209 (\"Ambiguous column name '<column-C>'\") at runtime when both filter conditions co-occur. With M callers of the shared <helper>, regression scope may extend beyond the N confirmed vulnerable procs once any consumer adds a <table-2> JOIN."`

### Pattern B — Type / contract narrowing (Rubrics A3, A4, A5, MTO-4)

- **`exploitability.rating`**: `easy` when the narrowed contract is hit by every existing caller; `moderate` when only edge-case inputs trigger overflow/truncation; `unknown` when the call-time data shape is invisible.
- **`exploitability.reasoning` template** (two-sentence shape — plain-language opener + `Concrete evidence:` continuation): `"An <plain-language-operation — e.g., 'INSERT path into `<table>`'> fails when callers pass <input-shape> exceeding the narrowed <new-constraint>. Concrete evidence: pre-PR contract was <old-constraint> at <file>:<line>; this PR narrows it to <new-constraint> at <file>:<line>; any caller relying on the old contract <fails-mechanism — e.g., 'overflows at conversion' / 'truncates silently' / 'violates the foreign key'>."`
- **`trace` template**:
  1. `Apply this PR (changes <type/constraint> at <file>:<line>).`
  2. `Invoke <consumer-proc> with <input-payload-that-exceeds-new-constraint>.`
  3. `Observe: <runtime-symptom — truncation / overflow / FK violation / migration failure>.`
- **`impact` template**: `"All callers passing <input-shape> see <failure-mode>; downstream <consumers/jobs/UIs> dependent on the old contract surface degraded behavior. Confirmed vulnerable sites: N; total grep-confirmed callers: M."`

### Pattern C — Twin-table / cross-MTO sync drift (Rubric MTO-4, C1–C5)

- **`exploitability.rating`**: usually `unknown` (customer-side migration patterns and sync-job schedules are not in the worktree); `moderate` when a sync-job stub is visible.
- **`exploitability.reasoning` template** (two-sentence shape; for cross-MTO drift the opener is mechanism-level since customer-side patterns are not in the worktree): `"A sync reconciliation between `<twin-X>` and `<twin-Y>` runs when the <sync-job-name> fires while the twin column/constraint/index sets diverge. Concrete evidence: `<twin-X>` at <file>:<line> holds <element-X>; `<twin-Y>` at <file>:<line> holds <element-Y>; customer-side trigger pattern not visible in the worktree, so rate unknown."`
- **`trace` template** (conditional shape):
  1. `Apply this PR (adds <change> to <twin-X> at <file>:<line>; does NOT update <twin-Y> at <file>:<line>).`
  2. `IF the <sync-job-name> runs OR a customer-side MTO-mirror reconciliation occurs, the divergent column-set will be detected.`
  3. `Observe: reconciliation step fails with <expected-error> OR results diverge silently between twins.`
- **`impact` template**: `"Twin-table mirror desync risk: <twin-X> and <twin-Y> hold divergent <element>. N twin pairs confirmed; M total twin-table relationships in the repo. Downstream sync-job consumers will fail or silently produce divergent results."`

### Pattern D — Raw-view ripple / chained-binding (Rubric D1)

- **`exploitability.rating`**: usually `unknown` (whether downstream views auto-rebind depends on `sp_refreshsqlmodule` schedule and consumer DDL state); `easy` when an indexed view is also affected.
- **`exploitability.reasoning` template** (two-sentence shape; the opener is at the view-binding level since whether downstream views auto-rebind is the actual mechanism): `"A query of `<downstream-view>` runs after this PR before `sp_refreshsqlmodule` has refreshed the binding. Concrete evidence: this PR modifies `<base-view>` at <file>:<line>; `<downstream-view>` at <file>:<line> selects via chained binding; the persisted projection becomes stale until refresh."`
- **`trace` template** (conditional):
  1. `Apply this PR (modifies <base-view> at <file>:<line>).`
  2. `WITHOUT running sp_refreshsqlmodule on <downstream-view>, query <downstream-view> via <existing-consumer>.`
  3. `Observe: stale/incorrect column binding OR persisted-data mismatch on indexed views.`
- **`impact` template**: `"Downstream views <view-list> may silently return stale bindings until sp_refreshsqlmodule is run repo-wide. N confirmed downstream consumers; M views in the repo."`

### Pattern E — Migration / DEFAULT / nullability rubrics (Rubric B, J)

- **`exploitability.rating`**: `easy` when an existing INSERT path doesn't supply the new column; `unknown` for greenfield migrations where no INSERT path is yet present.
- **`exploitability.reasoning` template** (two-sentence shape; the opener names the data path that hits the new column): `"An INSERT path into `<table>` reaches this defect when callers omit `<new-column>` (the pre-PR contract did not require it). Concrete evidence: this PR adds `<new-column>` NOT NULL without DEFAULT at <file>:<line>; pre-existing INSERT call sites at <file-A>:<line>, <file-B>:<line> omit it; SQL Server fails the INSERT for every such caller."`
- **`trace` template**:
  1. `Apply this PR (adds <new-column> to <table> as NOT NULL without DEFAULT at <file>:<line>).`
  2. `Execute INSERT INTO <table> (<old-column-list>) VALUES (<sample-values>) — any pre-existing INSERT call site.`
  3. `Observe: 'Cannot insert NULL into column <new-column>' OR migration step fails at deploy.`
- **`impact` template**: `"N existing INSERT call sites in the repo will fail without code change. Migration deployment will fail if the table has any pre-existing rows."`

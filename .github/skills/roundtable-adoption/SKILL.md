---
name: roundtable-adoption
description: Collect, query, audit, and render Roundtable PR adoption records from Azure DevOps using the installed roundtable adoption commands. Use for requests such as "which PRs were reviewed?", "break down findings by severity", direct PR record validation, collector audits, or "generate the adoption HTML". Do NOT use to run or replay code reviews (roundtable-review), create evaluation PRs (roundtable-evaluation-pr), modify adoption implementation, or publish/unpublish review findings.
---

# Roundtable Adoption

Use the installed adoption CLI to discover recorded Roundtable reviews and project them into local
queries or a standalone HTML audit report. A label identifies the latest review state; the
versioned metadata records provide execution history.

## Workflow

1. Choose the narrowest input:
   - For one known PR, require organization, project, repository, and numeric PR ID.
   - For repository or project adoption, use the requested repository and metadata date bounds.
   - If complete JSONL already exists, skip collection and use it directly.
2. Collect to a named file so progress stays on stderr and completeness is auditable:

   ```powershell
   roundtable adoption collect --org ORG --project PROJECT --repository REPO --pr PR_ID --out .\roundtable-reviews.jsonl
   roundtable adoption collect --org ORG --project PROJECT --repository REPO --out .\roundtable-reviews.jsonl
   roundtable adoption collect --org ORG --project PROJECT --since YYYY-MM-DD --out .\roundtable-reviews.jsonl
   ```

3. Before querying or reporting, read `<out>.audit.json`. Continue only when `status` is
   `complete`; treat `<out>.partial` as incomplete evidence.
4. Use the metric that matches the question:
   - `prs`: unique reviewed PRs; group by `project`, `repository`, `configurationName`,
     `installationSource`, `toolVersion`, or `verdict`.
   - `reviews`: review executions; use the same dimensions.
   - `findings`: Roundtable-reported findings; group by severity, category, or agent.

   ```powershell
   roundtable adoption query --input .\roundtable-reviews.jsonl --metric reviews --group-by configurationName
   roundtable adoption query --input .\roundtable-reviews.jsonl --metric findings --group-by severity
   ```

5. For manual quality auditing, generate and open the self-contained report:

   ```powershell
   roundtable adoption report --input .\roundtable-reviews.jsonl --out .\roundtable-adoption.html --open
   ```

   Use the prioritized audit queue to open the linked PR and inspect its published Roundtable
   comments. The HTML needs no local web server after generation.
6. Report the audit status, scope, counts, output paths, and any diagnostics. State collection
   gaps instead of extrapolating through them.

## Hard rules

- Do not run `roundtable review`, publish, or unpublish through this skill; route review execution
  to `roundtable-review`.
- `adoption collect`, `query`, and `report` are read-only. Run `adoption retry` only when the user
  explicitly asks to retry a failed ADO review-record write.
- Never query or report a partial collection as complete. Rerun collection or clearly label the
  result incomplete.
- Severity and finding counts describe what Roundtable reported, not finding correctness or
  remediation quality. Assess those manually from linked PR comments.
- Metrics exclude reviews made by clients that did not write v1 adoption metadata. Do not infer
  total organizational adoption beyond the collected records.

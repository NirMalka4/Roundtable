# ADO MCP Usage — Publish PR Comments (write)

> **Role policy for the `ado-publish` server.** This section tells you *why* and
> *when* to call the Azure DevOps MCP tools to resolve the PR and post comment
> threads / suggestions. The concrete tool names for each capability are listed in
> the **## ADO Tool Bindings** section of your context — always call the tool from
> that table's `Tool name` column verbatim.
>
> **Availability gate.** These tools are usable ONLY when a **## ADO Repository
> Identity** section is present in your context. If that section is absent, you
> cannot publish — emit the failure JSON payload with `"publish_status": "failed"`
> and `failure_reasons` noting the missing ADO identity.

Use the ADO Repository Identity **GUID** values (Project ID, Repository ID) when a
tool accepts them — GUIDs are preferred over names for reliability.

## Capability map

- **Resolve repository / PR**: use *Lookup repository* and *Find active PR* to
  locate the target PR for the current `source_branch`. If no active PR exists,
  use *Create draft PR* (always `isDraft: true` to avoid triggering CI/CD).
- **Read PR state**: use *Get pull request* (iteration number) and *List PR
  threads* (avoid duplicate comments) before publishing.
- **Publish**: use *Create PR thread* for inline and general comments. If thread
  creation fails entirely, fall back to *Update pull request* to append the
  suggestions into the PR description.

## Inline comment parameters (Create PR thread)

- `filePath` — must match the PR diff exactly (typically with a leading `/`)
- `rightFileStartLine` / `rightFileEndLine` — 1-based line numbers within diff hunks
- `rightFileStartOffset` / `rightFileEndOffset` — 1-based character offsets (minimum: 1)
- `status` — `"Active"` for all actionable comments
- `project` — always include to prevent resolution errors

## Operational guardrails

- **Retry on parameter errors**: if a call fails with "Project required" or
  "Invalid Offset", retry with corrected parameters (Project from the ADO
  Repository Identity, Offset ≥ 1).
- **No duplicates**: check existing threads before posting the same suggestion twice.
- **Draft mode**: a newly created PR is ALWAYS a Draft.
- **Graceful degradation**: if a write call cannot succeed, downgrade inline →
  file-scoped → general comment, then fall back to the PR description.

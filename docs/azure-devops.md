# Azure DevOps integration

Azure DevOps is Roundtable's only shipped repository, publisher, adoption, and
evaluation provider.

## Authentication

PR operations use `ROUNDTABLE_ADO_PAT` or `AZURE_DEVOPS_PAT` when configured.
The default `auto` mode otherwise uses the existing Azure CLI login. Feed
authentication is separate from PR authentication.

## Review and publication

A full URL contains the repository coordinates:

```text
roundtable review --pr <azure-devops-pr-url>
```

A numeric PR id requires the path to a local clone of the repository that owns
that PR:

```text
roundtable review <repo> --pr <id>
```

Both forms resolve the source and target commits and review a detached
workspace.

`roundtable publish <session>` creates eligible finding threads from lowest to
highest configuration-declared severity, then creates the closed executive
summary last. Azure DevOps displays newer threads first, so readers see the
summary followed by findings from highest to lowest severity. Findings with equal
severity retain projector order in the UI. Finding footers link their source-agent
responses; the summary links the local session and its HTML report. All footer
paths are relative to the configured artifacts root, and `roundtable report`
resolves those references through that root from any working directory.
`unpublish` removes that session's watermarked findings and executive summary.
Dry runs preserve the current JSON and terminal-report semantics. Anchoring,
iteration tracking, deduplication, labels, and failure behavior remain in the
Azure DevOps adapter.

## Adoption

Every successful real PR review persists its adoption record locally and attempts
to write a latest-state label:

```text
Roundtable-v1-<pep440-version>-<normalized-config>-<feed|local>
```

Recording failure does not alter the review verdict. Retry it with:

```text
roundtable adoption retry <session-dir>
```

When a review is published, its versioned adoption metadata is embedded in the
executive summary; adoption recording does not create a standalone PR comment.

Collection scans repositories and PR pages, checks labels, then reads threads
only for recognized labels. With `--out`, records stream to `<out>.partial`;
success atomically replaces the output and writes `<out>.audit.json`.

## Evaluation

`eval-pr` reviews first, pushes exact commits to disposable scratch refs, creates
an evaluation-only draft, and publishes the saved result. `eval-cleanup`
abandons drafts and removes only refs under
`refs/heads/roundtable/eval/`. It never completes or deletes a PR.

This supports a common postmortem question: could the defect have been detected
in review before the change reached production?

Recover a completed PR from its merge commit and evaluate its original source
and base:

```text
roundtable eval-pr --from-merge-commit <sha> --repo <path-or-ado-url> --name <evaluation-name>
```

Evaluate a PR only up to one source-history checkpoint:

```text
roundtable eval-pr --from-pr <ado-pr-url> --at-commit <sha> --name <evaluation-name>
```

Evaluate an explicit source/base pair:

```text
roundtable eval-pr --from-commit <sha> --base <sha-or-ref> --repo <path-or-ado-url> --name <evaluation-name>
```

Preview cleanup before abandoning the draft and removing its guarded branch:

```text
roundtable eval-cleanup --repo <path-or-ado-url> --name <evaluation-name> --dry-run
```

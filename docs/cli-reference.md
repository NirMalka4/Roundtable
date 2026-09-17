# CLI reference

| Command | Purpose |
|---|---|
| `run` | Run a configuration with repeated `--input KEY=PATH` source payloads. |
| `inputs` | Preview review inputs without AI. |
| `review` | Run a local or Azure DevOps PR review. |
| `publish` / `unpublish` | Publish or retract a saved review through its configured publisher. |
| `adoption collect` | Collect Azure DevOps v1 records to JSONL. |
| `adoption query` | Aggregate collected records offline. |
| `adoption report` | Render a self-contained HTML report. |
| `adoption retry` | Retry one failed ADO adoption-label write. |
| `eval-pr` / `eval-cleanup` | Manage Azure DevOps evaluation drafts and guarded refs. |
| `view` | Show a saved review result. |
| `report` | Render a saved session as HTML. |
| `tools` | Inspect or compare saved tool surfaces. |
| `index` / `prune` | Maintain local artifact indexes and retention. |
| `skill` | Manage the optional explicit-review skill. |
| `doctor` | Validate configuration and runtime capabilities. |
| `update` | Inspect or install versions from a configured package index. |

Run `roundtable <command> --help` for authoritative flags. `--config` accepts
`buddies`, `inspectorx`, or a bundle directory. Existing command names, `--pr`,
exit codes, and environment precedence remain compatible.

`view` and `report` accept either an absolute session path or a path relative to
Roundtable's configured artifacts root.

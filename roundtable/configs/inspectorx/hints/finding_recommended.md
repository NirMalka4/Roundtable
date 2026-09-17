One or more findings are missing recommended descriptive fields.

CORRECTIVE ACTION (advisory — not blocking):
- Give every finding a human-readable `title`, a `severity`, and a `description`.
- `id` is already mandatory; these fields make the finding usable in the published
  report and let reviewers triage it without opening the code.
- Keep the `description` specific to THIS change (what/where/why it matters), not a
  generic restatement of the rule.

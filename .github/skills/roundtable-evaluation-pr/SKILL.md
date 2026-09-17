---
name: roundtable-evaluation-pr
description: Create an evaluation-only Azure DevOps draft PR that reviews a cumulative PR checkpoint, merged-PR provenance, or explicit commit/base pair before PR creation, then publishes the saved findings. Use for requests such as "recreate this merged PR as an evaluation draft," "test when Buddies first finds this commit bug," or automating an evaluation PR. Do NOT use for ordinary PR, branch, or session reviews (roundtable-review), normal commit/push/PR delivery (ship-it), report-only comparisons, or Roundtable authoring.
---

# Roundtable evaluation PR

Create one disposable draft PR that proves whether the selected Roundtable configuration finds
issues in an exact historic diff. The review completes before the scratch refs or draft PR exist,
so neither the source PR description nor generated PR prose can bias reviewers.

## Workflow

1. Resolve one source:
   - PR tip or checkpoint: a full Azure DevOps PR URL, optionally with a source-history commit;
   - completed PR: its Azure DevOps merge commit plus a repository path or clone URL;
   - explicit cumulative pair: source SHA, base SHA, and repository.
2. Choose a unique, short evaluation name. A PR checkpoint is cumulative from the PR target to the
   selected source commit; it is not the selected commit's delta against its parent.
3. Use the requested configuration. Add `--config buddies` only when Buddies was requested;
   otherwise omit `--config` so the user's default configuration applies.
4. Run exactly one command:

   ```text
   roundtable eval-pr --from-pr <ado-pr-url> --name <name> [--config buddies]
   roundtable eval-pr --from-pr <ado-pr-url> --at-commit <sha> --name <name> [--config buddies]
   roundtable eval-pr --from-merge-commit <sha> --repo <path-or-ado-url> --name <name> [--config buddies]
   roundtable eval-pr --from-commit <sha> --base <sha> --repo <path-or-ado-url> --name <name> [--config buddies]
   ```

5. Read the JSON outcome. Report the saved session, exact source/base refs, draft PR URL, verdict,
   and whether publication completed. A `partial` outcome means the draft remains available but
   comments were not fully published.
6. State that the scratch refs and draft PR are still live, and offer to reclaim them:

   ```text
   roundtable eval-cleanup --repo <path-or-ado-url> --name <name> [--dry-run]
   roundtable eval-cleanup --repo <path-or-ado-url> --all [--dry-run]
   ```

   Offer it; do not run it in the same turn that created the evaluation. The comparison the
   evaluation exists to support usually happens after the run.

## Hard rules

- Do not open or summarize the source PR title or description for reviewers. The command resolves
  only repository identity and exact commit SHAs into the local review.
- Do not replace the command with cherry-pick, patch application, rebase, or hand-created branches.
- Do not infer a standalone commit's base from its immediate parent. Require `--base`; merge commits
  resolve through Azure DevOps `lastMergeCommit` provenance, including squash merges.
- Do not enable auto-complete, merge the evaluation PR, link or transition work items, or publish
  through `ship-it`.
- A failed review must leave no remote refs or PR. If publication fails after draft creation,
  preserve and report the draft instead of deleting evidence of partial state.
- A successful review leaves its refs and draft PR behind on purpose. Reclaim them only when the
  user accepts the offer, and never as a side effect of another request.
- Reclaim through `eval-cleanup`, never by deleting a pull request. Abandoning keeps the published
  threads readable and the commits resolvable, so cleanup costs none of the evaluation's evidence —
  which means preserving evidence is not a reason to leave the namespace dirty.
- Do not hand-delete evaluation refs through `git push` or the Azure DevOps API. The order is
  load-bearing and the namespace guard lives in the command.
- Do not run simulation: synthetic findings cannot test whether reviewers discover the target issue.

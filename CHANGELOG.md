# Changelog

All notable changes to Roundtable are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Releases are cut from this file together with `roundtable/_version.py`.** A
release is a pull request that (1) bumps `__version__` in
`roundtable/_version.py` and (2) promotes the **[Unreleased]** section below to
a matching `## [vX.Y.Z]` heading. CI's coherence gate
(`scripts/check_release_version.py`) fails the build if the two disagree. The
version is authored in those two files and nowhere else.

### How to add an entry

When you merge a user-facing change, add **one line** under **[Unreleased]**, in
the group that matches your change (add the `###` group if it isn't there yet).
Write it for a reader of the release notes: short, imperative, about *what
changed* — not how. Skip internal-only changes (refactors, test tweaks).

    ### Added
    - `--json` output flag for `roundtable review`.

Groups (from [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); use only
the ones you need, in this order):

- **Added** — a new feature or capability.
- **Changed** — a change to existing behavior.
- **Deprecated** — still works, but slated for removal.
- **Removed** — a capability that no longer exists.
- **Fixed** — a bug fix.
- **Security** — a vulnerability fix.

Leave the **[Unreleased]** heading itself in place — a release renames a *copy* of
its contents to the new version and leaves `[Unreleased]` empty for the next cycle.

## [Unreleased]


## [v4.6.6] - 2026-09-16

### Changed
- Publish closed ADO summaries above severity-ordered findings, and keep adoption metadata only in the summary and latest-state label.
- Document the exact Microsoft dependency index and separate Roundtable Azure Artifacts update source for bootstrap.
- Validate a deterministic hosting-neutral README substitution for future public snapshots.

### Fixed
- Keep local usernames, branch names, and absolute artifact-root paths out of ADO footers by publishing resolvable privacy-safe session aliases.

## [v4.6.5] - 2026-09-14

### Changed
- Publish PR summaries first, order finding threads by each configuration's severity scale, and include configuration and local session commands in every thread.

## [v4.6.4] - 2026-09-09

### Added
- Record successful PR reviews with durable metadata and one current-state label, with project-scoped JSONL collection, local querying, retry, and an offline HTML dashboard with a prioritized, linkable audit queue.

### Fixed
- Bound adoption collection memory and label-inspection concurrency, with direct PR collection, progress, audit metadata, and interruption-safe JSONL output.
- Make Windows installations expose `roundtable` and `rt` immediately in the installation terminal.

## [v4.6.3] - 2026-09-08

### Changed
- Automatically validate live models and version-matched built-in tools before doctor and real Copilot reviews, caching non-sensitive tool names per user after two bounded probe turns.

### Fixed
- Block authenticated doctor and review when runtime capability discovery fails instead of continuing with stale packaged tool metadata.

## [v4.6.2] - 2026-09-06

### Fixed
- Reject duplicate agent keys, keep candidate output details out of persisted traces, and validate each configuration against its own bundle resources.

## [v4.6.1] - 2026-09-06

### Fixed
- Preserve withheld reviewer proposals in `verdict.md` beside Remedy Scout's rationale and evidence, clearly marked as unapproved.

## [v4.6.0] - 2026-09-06

### Changed
- Buddies reviewers now close one evidence-backed remediation design, explain why it addresses the finding, and align its illustration with repository precedent.
- Buddies groups each inline finding into an evidence-backed issue and a concise `Suggested remediation` paragraph with support and known limitations.
- Buddies remediation is now one non-applyable reviewer draft that the read-only post-Judge Remedy Scout must independently qualify before publication.
- Withhold missing, invalid, or unsupported remediation from PR comments without changing the Judge verdict, while recording the reason in `verdict.md`.

## [v4.5.0] - 2026-09-03

### Changed
- Report SDK-measured AI Credits in human artifacts only when complete; persist raw `totalNanoAiu` and machine-only `totalPremiumRequestCost` with explicit provenance instead of estimating billing from model events.

## [v4.4.1] - 2026-09-02

### Fixed
- Match an existing local clone when a pull request names Azure DevOps by its `{org}.visualstudio.com` host, instead of re-cloning the same repository into a second cache slot.
- Enable Windows long-path support during `git clone` itself, so cloning a deep repository into the cache no longer fails its checkout.
- Report the git diagnostic that explains a failed clone rather than the leading progress output.

## [v4.4.0] - 2026-09-01

### Added
- `roundtable eval-cleanup` to abandon evaluation draft PRs and delete their scratch refs.
- `python scripts/release.py promote` folds pending `[Unreleased]` notes into the release heading a branch has already bumped to.

### Changed
- Buddies PR comments are written for a human reader: a plain-language claim title, a short "Why", a ready-to-apply suggestion, and Judge-owned grounding.
- Buddies severity must name what bounds it, and a claim's blocking effect is derived from its ruling instead of being asserted alongside it.
- The Buddies Judge adjudicates reviewer claims only; it no longer authors, rewords, or receives remediation prose.
- Rejecting or discounting a peer reviewer's finding now demands the same grounding as upholding it.
- Findings are indexed from reviewers only, so a reducer echoing its own input no longer counts as coverage.
- Each Buddies reviewer is bounded to a single concern, so a runtime-correctness proof reaches the reader through Counter Case alone rather than from two competing sources.
- Counter Case records why it dropped each candidate, and must exhibit the composed artifact it read whenever it claims a counter-case holds.
- Report the backend's own classification of a failed agent turn (for example `rate_limited`) in logs, artifacts and the HTML report, in place of an exit code that collapsed every cause onto one value.
- The release-coherence gate decides for itself whether a branch publishes — its authored version leads `origin/main` — and demands promoted notes at author time instead of leaving it to the publish job after the merge to `main`.

### Removed
- Two Buddies Judge gates that scored reasoning quality, which a deterministic check cannot enforce.

### Fixed
- Drop internal review bookkeeping from published PR comments.
- Let the packaged review skill's helper script run directly and read tool output as UTF-8.
- The review skill reports the verdict the run recorded instead of re-deriving its own, which could summarize a rejected review as approved.
- Keep an agent's output when the backend process faults after the output was already submitted and accepted, instead of discarding the run.
- Unpromoted release notes pointed at `release.py bump`, which cuts a further version; they now point at the command that folds them into the version being published.

## [v4.3.0] - 2026-08-17

### Added
- Add `roundtable eval-pr` for bias-free cumulative review of PR checkpoints, merged PR provenance, or explicit commit/base pairs before publishing through an evaluation-only Azure DevOps draft.

## [v4.2.1] - 2026-08-16

### Fixed
- Install the Windows CLI as a user-scoped application with a complete app-and-skill uninstall path, and make Buddies simulation produce an accepted synthetic Judge verdict.

## [v4.2.0] - 2026-08-16

### Added

- Add an explicit-request Roundtable review skill and commands to install, inspect, or remove its
  packaged user-scoped mirror safely. Completed reviews produce a non-recursive actionability brief
  that explains which adjudicated claims to address, investigate, treat as optional, or dismiss.

## [v4.1.0] - 2026-08-13

### Changed

- Make Buddies the default review configuration; select InspectorX explicitly with
  `--config inspectorx`.

## [v4.0.0] - 2026-08-12

### Added

- `--config <bundle>` picks the review configuration for a single invocation
  (`roundtable review --config buddies …`, `roundtable doctor --config buddies`),
  so switching bundles no longer means exporting `ROUNDTABLE_CONFIG_ROOT`.
- `--concurrency` (plus `ROUNDTABLE_CONCURRENCY` / `roundtable.yaml`) sets how many
  agents run at once. The pool was a hard-coded 4, so a sixth reviewer waited for a
  free slot with nothing to say why.
- **CounterCase**, a sixth Buddies reviewer, which proves a behavioral divergence by
  constructing a reachable input and tracing it to the wrong result.
- A finished review prints clickable links to its session directory, its verdict and
  its HTML report, and always writes `report.html` instead of leaving it to a
  separate `roundtable report` run.
- `doctor` refuses an agent whose `model:` or `tools:` the runtime does not serve,
  and `review` runs that same check as a preflight — a withdrawn or misspelt name
  can no longer reach a live run.
- Sessions record the model the runtime actually served alongside the declared one,
  and the session report rules on each agent's MCP tool use against its declared
  grant.
- `--input-from <session-dir>` recreates the recorded commit, workspace overlay,
  hints, and ADO context before replaying saved source payloads, with configuration
  drift reported before execution.
- The public `Engine` can run multiple explicit configurations in one process,
  with a fresh configuration-scoped backend for each run.
- Sessions retain typed, redacted SDK events and explicit omission counts so tool
  activity and execution flow remain inspectable.

### Changed

- **BREAKING:** Python integrations now consume typed package facades and call
  `Engine.run(configuration, inputs)` instead of reaching through review-specific
  configuration, runtime, persistence, or orchestration internals.
- Configuration, backend selection, execution, persistence, and reporting are
  run-scoped; two configuration bundles can execute sequentially without sharing
  ambient bundle state or cached agent definitions.
- `doctor` and `review` now print the **same** effective-config table, from one
  shared builder. `doctor` also names the active bundle and its root.
- A parameter left unset says what takes over
  (`base_branch = (unset -> auto-detect origin/HEAD, else origin/main)`) instead of
  a bare `(unset)`.
- A clean `doctor` reports only what it found. The import-boundary and rebrand
  checks still run on every invocation and still fail with a non-zero exit.
- Buddies agents declare concrete runtime tool names. The previous alias vocabulary
  (`search`, `web`, `todo`) expanded to nothing, so every reviewer ran with no code
  search while appearing to have it.
- Every Buddies agent gets a uniform 12-minute budget.

### Removed

- The deprecated `inspectorx-py` command; use `roundtable` or `rt`.
- The `grace_multiplier` graph key, which nothing ever read.

### Fixed

- A PR review's banner reported `branch=None`, and its `ado_mcp`, `mcp_overrides`
  and `models_routed` counters each reported something other than their name.
- An agent's declared `model:` never reached the session, so every review silently
  ran the runtime's ambient default.
- The graph's per-agent `timeout_seconds` was parsed and then dropped by every caller, so
  a declared 30-minute budget was inert and every agent ran on 600s.
- A failed agent run names its real cause — a timeout and a rejected output contract
  read differently, because they have opposite fixes.
- The verdict says which reviewers did not report, instead of presenting a partial
  roster as a complete one.
- Publishing an old session projects it through that session's own configuration
  bundle rather than whichever one the current process happens to hold.
- A `contains` schema failure names the member that was missing, so the retry
  feedback an agent reads says what to add.

## [v3.1.0] - 2026-08-06

### Added

- **A second review configuration, Buddies** — a peer-review roundtable (Big-O,
  North Star, Red-Green, Smell Check, Taint Check) whose Judge argues against every
  reviewer finding before any of it reaches the pull request. Point
  `ROUNDTABLE_CONFIG_ROOT` at `roundtable/configs/buddies` to run it.
- Buddies publishes what it adjudicated: one inline thread per surviving claim —
  each carrying the ruling, the evidence behind it, and a one-click fix only when
  the fix was actually verified — plus a verdict comment with the Judge's summary,
  an index of the threads posted, the claims the severity floor held back, and a
  per-reviewer box score.
- A configuration bundle can now own its **output-validation gates**, its
  **session-report renderer**, and its **publish projector and PR comment
  renderer**. The engine keeps the mechanism; a bundle that speaks its own review
  vocabulary no longer has to teach the engine that vocabulary.
- Sessions record the machine that produced them (`trace.json` `provenance.host`),
  and every published comment names that host beside the artifacts path — the path
  means nothing anywhere else.
- Reviewer file excerpts are read from the review checkout rather than quoted by
  the model, so a paraphrased or elided quote is impossible by construction.
- A `--pr`/URL review's worktree reuses the clone's installed `node_modules` when
  the diff provably touches no lockfile, so a reviewer that runs the target's own
  tooling does not reinstall from scratch.

### Changed

- `--min-severity` and `--publish-min-severity` now offer the severity scale the
  active configuration declares, instead of a hardcoded five-level list.
- Their default floor is now configuration-owned: InspectorX keeps `medium`,
  while Buddies uses `low` so its full severity scale publishes by default.

### Fixed

- An `UNKNOWN` verdict now says which failure produced it. An agent that never ran,
  one that answered with nothing, and one that spent its whole retry budget failing
  output validation were reported identically — the last means the review is
  unusable and cost real money, the first only that it is incomplete.
- A one-click suggestion is anchored at the span it actually rewrites. It was
  anchored at the finding's first location, so clicking Apply could paste a fix
  over unrelated lines.
- `sink: null` is honoured instead of silently defaulting to the Azure DevOps
  publisher.
- `verdict.md` labels each finding with its real severity instead of a hardcoded
  `CRITICAL`.

## [v3.0.0] - 2026-07-29

### Changed

- **BREAKING: the engine is renamed InspectorX -> Roundtable.** The Python
  package (`inspectorx_py` -> `roundtable`), the console command (`inspectorx-py`
  -> `roundtable`, with a short `rt` alias), and every `INSPECTORX_*` environment
  variable (-> `ROUNDTABLE_*`) now carry the new name. The runtime home tree moved
  from `~/InspectorX-py` to `~/roundtable`. The default review **config bundle**
  keeps its `inspectorx` identity — only the generic engine was renamed.

### Added

- One-release back-compat so an existing install keeps working while you migrate:
  the deprecated `inspectorx-py` command still runs (with a warning); legacy
  `INSPECTORX_*` env vars and an `inspectorx.yaml` config file are still honored;
  and the `~/InspectorX-py` runtime tree is migrated to `~/roundtable` on first
  run. Previously published PR comments and version labels (`InspectorX-CLI-*`)
  are still recognized for dedup, unpublish, and relabel.

### Deprecated

- The `inspectorx-py` command, `INSPECTORX_*` env vars, `inspectorx.yaml`, and the
  `InspectorX-CLI` PR watermark/label are deprecated and will be removed in a
  future release. Switch to `roundtable`/`rt`, `ROUNDTABLE_*`, and `roundtable.yaml`.

### Fixed

- `roundtable update` on Windows no longer fails with a file-lock error
  (`WinError 32`), or leaves a corrupted half-uninstalled install, when launched
  via the `roundtable.exe` console script; the upgrade is handed off to a
  detached helper that waits for the launcher to exit before running pip, then
  records the result to a status file.

## [v2.0.1] - 2026-07-23

### Added

- `roundtable --version` prints the installed version and exits (works with no
  subcommand).

## [v2.0.0] - 2026-07-22

### Changed

- **BREAKING: Roundtable is now SDK-only.** The pipeline drives the model
  through `github-copilot-sdk` (promoted to a **core** dependency); there is no
  longer a backend choice. Installs now require the SDK — a missing SDK fails at
  startup instead of silently falling back.
- Reviews now run hermetically isolated from the operator's ambient `~/.copilot`:
  custom instructions, skills, and config discovery are disabled and session state
  is written to an ephemeral per-review directory, so a review's result no longer
  depends on the machine it runs on.
- The per-attempt tool-call log in `usage-summary.json` now records each MCP tool
  call's identifying arguments (which pull request / thread / repository it acted
  on) and every call's success or failure, not just builtin file reads.
- Per-agent token telemetry now includes the **input**, **cached-input**
  (`cacheReadTokens`), **cache-write**, **reasoning**, and **total** token
  breakdown — surfaced in `trace.json`, `usage-summary.json`, the `verdict.md`
  cost footer, and the session report (both the overall header and each agent's
  detail panel). Previously only output tokens were tracked (the CLI backend never
  exposed input/cache counts; the SDK does).
- The session report's **Tool usage** tile now splits **builtin** vs **MCP** call
  counts and lists every MCP server loaded this session, flagging any that loaded
  but no agent invoked — mirroring the `toolUsage`/`mcpUsage` split already in
  `usage-summary.json`.
- Telemetry now records the model's **stop reason** when it is abnormal: an agent
  whose response was truncated (`length`) or content-filtered surfaces under
  `usage-summary.json` `anomalies.truncatedOrFilteredAgents`, in `trace.json`
  (`anomalousFinishReasons`), and in the `verdict.md` cost footer — the direct
  explanation for an empty or malformed agent output.

### Removed

- The session report's per-agent **Est. context tokens**, **Context chars**, and
  **Response chars** metrics — chars/4 estimates and character counts that the real
  SDK token telemetry (input/cached/output) now supersedes. Their now-unused
  `contextChars`/`responseChars` producers were dropped from the per-agent
  `manifest.json` dump as well.
- **BREAKING: the `copilot` CLI subprocess backend** and its selector — the
  `backend` config key / `ROUNDTABLE_PY_BACKEND` environment variable no longer
  exist.
- **BREAKING: the `copilot_bin` and `copilot_home` settings** and the on-disk
  hermetic-home policy — isolation is now enforced by the SDK session flags
  described above, and the MCP set the model sees is bounded by what Roundtable
  passes it.
- **BREAKING: the `spike` command.**
- **BREAKING: the `--default-model` flag, the `ROUNDTABLE_DEFAULT_MODEL`
  environment variable, and the `default_model` config key** — they had no effect
  on model routing (which is graph-driven per agent) and are removed.

### Fixed

- MCP servers are now actually loaded: the resolved registry configuration was
  read as a file path and silently discarded, so no MCP server (e.g. Azure
  DevOps) was ever passed to the model. The publish-MCP acceptance check no longer
  false-fails when an agent successfully uses an ADO tool.

## [v1.0.1] - 2026-07-20

### Fixed

- **PR inline comments no longer render a truncated headline or drop the Issue
  section.** When an agent emitted no short title, `publish` used to promote the
  full description into the one-line headline, overflowing it and collapsing the
  explanation; comments now headline the finding id and render the description as
  the Issue body. Executive-summary rows fall back to the id, and duplicated
  ordinals in grounding traces (`1. 1.`) are removed.

## [v1.0.0] - 2026-07-16

### Added

- **First versioned release of Roundtable** — establishes SemVer
  versioning and this changelog as the per-version record.
- **Versioned distribution model.** The version is authored in
  `roundtable/_version.py`; a release PR promotes this file's `[Unreleased]`
  section to a versioned heading. The reviewed promotion PR is the release gate.
- **`roundtable update`** installs and upgrades the tool from an explicitly
  configured package index.
- **`scripts/check_release_version.py`** — the coherence gate that keeps
  `_version.py` and the top CHANGELOG heading in lock-step.

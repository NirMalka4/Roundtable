# Roundtable

Roundtable is a generic graph engine with shipped code-review bundles. Read
`AGENTS.md` for task routing and `docs/architecture.md` before changing package
ownership or extension contracts.

## Safety boundaries

- Keep `roundtable.engine` provider-independent and pass `Configuration` into
  `Engine.run`. Put platform mechanics in the adapter and import top-level
  packages through their facades; validate with
  `python scripts/check_package_boundaries.py` and affected contract tests.
- Copilot and the deterministic mock are the only shipped backends; Azure DevOps
  is the only shipped platform implementation. Do not add production providers
  or placeholders without an explicit requirement; use test fakes to prove
  neutral contracts and validate with `python scripts/gates.py`.
- Bundles register enrichers, extractors, gates, projectors, and reports through
  `roundtable.plugins`; do not make generic packages import a bundle. The package
  boundary and static-doctor gates must remain green.
- Declare exceptional per-agent limits in graph `tool_policy` with concrete SDK
  tool names; never branch on an agent identity in the backend. Keep policy in
  the graph and validate it through the static-doctor gate.
- For schema-backed LLM nodes, use the engine-provided
  `roundtable_submit_output` transport and accept only validated submissions.
  Do not declare that reserved tool in graph or MCP configuration, and do not
  treat raw assistant text as valid output for that node. For schema-less nodes,
  forward the first successful backend response unchanged; validate both paths
  with backend and engine tests.
- Replay must restore recorded tool-visible repository and provider context or
  fail; never downgrade to payload-only execution. Run
  `python -m pytest tests/unit/inputs/test_replay.py -q` and confirm non-zero
  tests pass.
- Keep secrets out of artifacts. Persist only retention-policy projections that
  identify redaction and truncation; run the persistence and backend retention
  tests covering the changed path.
- Keep shipped runtime, prompts, examples, tests, and fixtures portable: use
  synthetic provider identities such as `contoso`, `ExampleProject`, and
  `ExampleRepo`; never add private endpoints or machine-local MCP paths.
- Do not use live review, publication, adoption, evaluation, or external-state
  mutation in tests. Use the registered mock backend, fake adapters, and
  temporary directories; the relevant targeted tests and full gate must pass.
- Release preparation uses
  `python scripts/release.py manual-release X.Y.Z --date YYYY-MM-DD`. It updates
  the authored version and changelog, then runs the local gate set; publishing
  remains a separate maintainer action.
- The default bootstrap replaces the user-level Roundtable tool and updates
  future-shell PATH. Do not run it for routine validation; use
  `python scripts/bootstrap.py --dry-run`. Use `--contributor` only for requested
  checkout-local setup; validate installer changes with
  `python -m pytest tests/unit/scripts/test_bootstrap.py -q`.
- Do not re-baseline a failing fixture or weaken a gate to make validation pass.
  Fix the source behavior or update an expectation only for an intentional
  requirement change; `python scripts/gates.py` must then pass.

Before handing off code, run `python scripts/gates.py` from the repository root.
If it cannot run, report the blocker and the unverified criteria instead of
claiming completion.

Maintainers should revalidate this carrier when extension contracts, bootstrap
behavior, package-boundary gates, or Copilot instruction-surface support changes.

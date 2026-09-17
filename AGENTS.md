# Roundtable repository instructions

Use this file as a router. Installation and CLI usage are in `README.md`; the
authoritative pre-flight is `python scripts/gates.py` (`CONTRIBUTING.md`).

## Non-derivable rules

- For an explicit user-requested review in a supported scope, use
  `.github/skills/roundtable-review/SKILL.md` instead of reviewing directly or dispatching
  specialist reviewers. Higher-priority runtime instructions still govern. Routine self-checks
  while implementing work are not explicit review requests.
- `roundtable/configs/<bundle>/agent_graph.yaml` is the agent-graph source of
  truth. Its accepted document shape is
  `roundtable/graph/config_meta.schema.yaml`; do not copy that key list into prose.
- Declare exceptional per-agent execution limits in graph `tool_policy`, keyed
  by concrete SDK tool names; never branch on an agent identity in the backend.
- Keep the engine and neutral contracts provider-independent. Add platform REST,
  authentication, adoption, publication, and evaluation mechanics under the
  platform adapter (`roundtable.ado` for the shipped provider), not under
  `roundtable.engine` or neutral cores. Use the narrow contract described in
  `docs/architecture.md`; `python scripts/check_package_boundaries.py` and the
  affected contract tests must pass.
- Copilot and the deterministic mock are the only shipped backends; Azure DevOps
  is the only shipped platform implementation. Do not add a production backend
  or platform placeholder without an explicit product requirement; prove a
  contract with a test fake instead, then run `python scripts/gates.py`.
- `Publisher` is the canonical delivery contract. Preserve `Sink` names as
  compatibility aliases instead of extending them as a second contract;
  `tests/unit/output/test_publisher.py` must pass.
- An executable node declares `kind`; deterministic functions use `code_fn`, and
  dependencies use `edges`. Runtime dispatch and the valid kind set come from
  `roundtable.engine.NODE_HANDLERS`.
- A bundle registers enrichers, extractors, gates, projectors, and reports only
  through `roundtable.plugins`.
- Import another top-level package through its `roundtable.<package>` facade.
  `python scripts/check_package_boundaries.py` enforces this for every package.
- Pass `Configuration` into `Engine.run`; engine code must not read ambient
  configuration. File-backed and programmatically loaded documents use the same
  `Configuration` model.
- An LLM node with `output_schema` automatically receives the reserved
  `roundtable_submit_output` engine transport. Do not declare it in graph `tools`
  or MCP configuration, and do not treat raw assistant text as valid output for
  that node; only an accepted submission is canonical.
- An LLM node without `output_schema` has no structured-output contract. Forward
  its first successful backend response unchanged; do not parse, validate, or
  retry it as JSON.
- Keep secrets out of artifacts. Persist only retention-policy projections that
  identify redaction and truncation.
- Keep public/default runtime, prompts, examples, tests, and fixtures free of
  organization-specific identifiers and private endpoints. Azure DevOps remains
  a supported adapter; use synthetic provider identities in examples.
- Replay must restore the recorded tool-visible repository and ADO context or
  fail; never downgrade to payload-only execution. `tests/unit/inputs/test_replay.py`
  enforces the restoration and rejection paths.
- Do not run live review or publishing flows in tests. Use the registered mock
  backend and temporary directories.
- Release preparation uses
  `python scripts/release.py manual-release X.Y.Z --date YYYY-MM-DD`. It updates
  the authored version and changelog, then runs the local gate set; publishing
  remains a separate maintainer action.
- The default `python scripts/bootstrap.py` replaces the user-level Roundtable
  tool and updates future-shell PATH. Do not run it as a routine validation
  command; use `python scripts/bootstrap.py --dry-run`. Use
  `--contributor` only when a checkout-local development environment is
  requested; `tests/unit/scripts/test_bootstrap.py` validates both modes.

## Task routing

| Task | Start here |
|---|---|
| Explicit PR, committed-branch, or frozen-session review | `.github/skills/roundtable-review/SKILL.md` |
| Exact-diff evaluation draft and finding publication | `.github/skills/roundtable-evaluation-pr/SKILL.md` |
| Graph model, loading, fingerprint, predicates | `roundtable/graph/__init__.py` |
| Bundle paths and shipped roots | `roundtable/bundle/__init__.py` |
| User/workspace settings | `roundtable/settings/__init__.py` |
| Scheduling, node kinds, run results | `roundtable/engine/__init__.py` |
| SDK/mock backend implementations | `roundtable/backend/__init__.py` |
| Generic workflow application entry point | `roundtable/application/__init__.py` |
| Repository/change-request provider contracts | `roundtable/providers/__init__.py` |
| Publisher/projector/report contracts | `roundtable/delivery/__init__.py` |
| Adoption records, codec, query, report contracts | `roundtable/adoption/__init__.py` |
| Evaluation ordering contract | `roundtable/evaluation/core.py` |
| Azure DevOps adapters | `roundtable/ado/__init__.py` |
| Prompt composition and agent identity | `roundtable/runtime/__init__.py` |
| Frozen-session capture and exact replay | `roundtable/inputs/replay.py` |
| Output validation | `roundtable/validation/__init__.py` |
| PR adoption collection, querying, retry, and HTML reporting | `.github/skills/roundtable-adoption/SKILL.md` |
| Roundtable prompt-body authoring/review | `.github/skills/roundtable-agent-forge/SKILL.md` |
| Add or modify one graph node | `.github/skills/roundtable-add-agent/SKILL.md` |
| Create a configuration bundle | `.github/skills/roundtable-add-config/SKILL.md` |
| Register, validate, or scope MCP | `.github/skills/roundtable-manage-mcp/SKILL.md` |
| Prepare a release or repair release metadata | `scripts/release.py` |
| Architecture decisions | `docs/decisions/` |

## Validation

Run `roundtable doctor` after graph, prompt, schema, gate, or plugin changes.
Before handing off any code change, run:

```bash
python scripts/gates.py
```

Do not re-baseline a failing fixture or weaken a gate. Fix the source of drift.

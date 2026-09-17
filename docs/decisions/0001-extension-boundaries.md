# ADR 0001: Stable extension boundaries

**Status:** Accepted (2026-09-14)

Roundtable has separate contracts for LLM backends, repository/change-request
providers, publishers, adoption providers, and evaluation providers. Their
lifecycles, authorization, and capabilities differ, so a universal provider
interface would couple unrelated integrations.

Copilot remains the only production LLM backend. Azure DevOps remains the only
production repository, publisher, adoption, and evaluation provider. Test fakes
prove extension points; they are not shipped provider implementations.

The engine depends only on backend and provider-neutral contracts. Concrete
providers register at composition boundaries and own their native policies.
Azure DevOps REST collection, review-record retries, scratch refs, draft pull
requests, and evaluation cleanup live under `roundtable.ado`; neutral adoption
and evaluation packages retain compatibility exports and provider-free models.

The CLI remains the provider-aware composition root. Splitting its generic run,
publisher, adoption, and evaluation parser fragments into pass-through command
modules was rejected because it would relocate imports without changing any
dependency direction. Provider mechanics remain outside the CLI and engine.

**Rejected:** provider-name branches in the engine; a universal provider object;
Claude or GitHub placeholders that appear usable but fail at runtime; command
modules whose only purpose is file organization.

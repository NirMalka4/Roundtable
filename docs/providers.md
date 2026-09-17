# Platform provider contracts

Roundtable separates four platform boundaries because they have different
lifecycles and permissions.

## Repository and change request

`roundtable.providers` defines `ProviderId`, `RepositoryIdentity`,
`ChangeRequestIdentity`, `RevisionProvenance`, `RepositoryProvider`, and a
duplicate-safe registry. Neutral identity contains a provider, host, locator,
display name, revisions, and canonical URLs. Provider-only values belong in the
opaque extensions map.

A provider parses and canonicalizes remotes and change-request references. It
must reject credentials in persisted URLs. Azure DevOps is the sole registered
production provider.

## Publisher

`roundtable.delivery` defines `Publisher`, `PublicationTarget`,
`PublishRequest`, `RetractRequest`, and `PublishOutcome`. Bundle projectors
produce destination-neutral `PublishableResult` values; a publisher performs
the side effect only after an explicit publish command.

`Publisher` is canonical. Sink names remain aliases under the compatibility
policy. A graph may declare `publisher:` or legacy `sink:`, not both. Null means
the workflow does not publish.

## Adoption

An `AdoptionProvider` may record, collect, retry, label, and resolve links.
Unsupported capabilities must fail explicitly. Query and HTML projection consume
generic in-memory adoption records.

The `AdoV1Codec` remains strict: it emits the existing schema-version 1 shape,
marker, label, and `feed|local` acquisition values without a provider field.

## Evaluation

Evaluation core owns review-before-mutation ordering, outcomes, and failure
propagation. An `EvaluationProvider` owns remote staging, change-request
creation, and cleanup. Azure DevOps additionally owns the guarded
`refs/heads/roundtable/eval/` namespace.

Future providers should implement these contracts and conformance tests. This
repository intentionally contains no GitHub adapter or stub.

## Contribution checklist

1. Implement only the boundaries the platform supports; do not create
   success-shaped stubs for unsupported operations.
2. Keep native URLs, authentication, pagination, rate limits, and remote side
   effects inside the platform adapter.
3. Register repository identity through `roundtable.providers`; register
   publishers and adoption behavior through their separate contracts.
4. Add duplicate-registration, unknown-provider, identity, and capability
   failure tests. Use `tests/unit/providers/test_providers.py` as the repository
   provider starting point.
5. Run:

   ```text
   python -m pytest tests/unit/providers/test_providers.py -q
   python scripts/check_package_boundaries.py
   python scripts/gates.py
   ```

# ADR 0002: Compatibility and versioned readers

**Status:** Accepted (2026-09-14)

`Publisher` is canonical while `Sink`, `SinkOutcome`, `get_sink`, `sink:`, and
`AzureDevOpsSink` remain compatibility aliases. Graph documents may use
`publisher:` or `sink:`, but never both. An omitted key retains the historical
Azure DevOps default; an explicit null disables publication.

Repository and change-request identities use neutral fields plus opaque provider
extensions. New replay captures use v2 neutral identity. The reader continues to
accept replay v1 and restores its exact repository and ADO context.

Adoption keeps the strict Azure DevOps v1 marker, label, and JSON wire shape.
Decoded records carry provider identity in memory only. No generic adoption wire
v2 will be designed until a second provider supplies real requirements.

Existing command names, `--pr`, artifact readers, Buddies, InspectorX, updater,
and feed-backed package formats remain supported for this compatibility period.
Operational availability of pipeline automation is not a compatibility
guarantee. Removing an alias or old reader requires a separately approved major
release with a documented rollback.

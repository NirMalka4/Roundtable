# Compatibility and deprecation

| Existing surface | Canonical surface | Compatibility rule |
|---|---|---|
| `Sink` | `Publisher` | Alias retained; same behavior |
| `SinkOutcome` | `PublishOutcome` | Alias retained; same wire-free value |
| `get_sink()` | `get_publisher()` | Alias retained |
| graph `sink:` | graph `publisher:` | Both read; defining both is invalid |
| `AzureDevOpsSink` | `AzureDevOpsPublisher` | Alias retained |
| `CopilotResult` | `RunResult` | Alias retained |
| `AdoIdentity`, `PrReference` | neutral provider identities | Existing exports retained |
| replay v1 | replay v2 | v1 remains readable and exactly restorable |
| adoption ADO v1 | generic in-memory record | v1 bytes and accepted values remain unchanged |

Existing CLI commands and flags remain supported. Compatibility aliases are not
scheduled for removal in the current release line.

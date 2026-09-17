# Configuration bundle contract

Validated against `roundtable/graph/model.py`, `roundtable/engine/core.py`, and
`roundtable/graph/config_meta.schema.yaml` on 2026-08-10.

A bundle root contains one `agent_graph.yaml` plus only the prompt, schema, gate, hint, and plugin
files that document references. `Configuration.from_document(document, root=...)` validates the
same document shape that `Configuration.from_file(root)` loads.

Generic configurations need no review vocabulary or `enums.yaml`. When a run feature needs ordered
domain values, declare optional `domain_values` in `agent_graph.yaml`: point `schema` at an existing
vocabulary under `schemas/`, or use inline `values` only when no schema already owns them. The
resolved typed values belong to that `Configuration` and are included in its fingerprint and
session identity.

Begin with one source and one consumer/sink. Explicitly choose the executor, sink cardinality, and
publishing behavior; null sink means no publishing adapter. Add a projector or report only when the
domain result requires one. When publishing uses severity filtering, declare
`publishing.default_min_severity` from that configuration's `domain_values.severity`; null or
absence publishes all severities by default. Add plugins only for behavior unavailable in
graph/schema/gates.

Every external input is passed under its source-node key:

```python
from roundtable.engine import Engine
from roundtable.graph import Configuration

configuration = Configuration.from_file(bundle_root)
run = Engine("mock").run(configuration, {source_key: source_payload})
```

The mock backend exercises scheduling, prompt composition, schema-backed submission validation,
schema-less text forwarding, and result assembly without tokens or live I/O. Assert the actual sink
result and validity; “did not throw” is not a smoke criterion.

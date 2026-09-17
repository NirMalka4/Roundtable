# LLM backends

A backend is registered by name with a session-scoped provider:

```python
from contextlib import contextmanager

from roundtable.backend import BackendCapabilities, register_backend

@contextmanager
def open_example(options):
    yield ExampleBackend(options)

register_backend(
    "example",
    open_example,
    lambda: BackendCapabilities(structured_output=True, tools=True),
)
```

`ExampleBackend.run(RunRequest) -> RunResult` receives the agent key, prompt,
working directory, timeout, optional reusable session id, validation context, and
optional structured-output submission transport. A schema-backed node is
successful only after that transport accepts a value. A schema-less node returns
the first successful backend response unchanged.

Backends normalize timeouts, cancellation, outcome classification, usage,
optional cost, diagnostics, and retention facts into `RunResult`. SDK-native
tool limits stay in the adapter. Graph `tool_policy` keys name concrete SDK tools
and are not interpreted by the engine.

Use the deterministic mock and backend contract tests as the contribution
template. A backend must register without an engine edit. Copilot is currently
the only shipped production backend; no Claude adapter or dependency is present.

## Contribution checklist

1. Implement `Backend.run(RunRequest) -> RunResult`.
2. Register a session-scoped provider and declare its `BackendCapabilities`.
3. Keep vendor authentication, SDK events, tool policy, and diagnostics inside
   the adapter.
4. Fail explicitly when a requested capability is unsupported.
5. Add the adapter's focused tests and extend
   `tests/unit/backend/test_contract.py`.
6. Run:

   ```text
   python -m pytest tests/unit/backend/test_contract.py -q
   python scripts/gates.py
   ```

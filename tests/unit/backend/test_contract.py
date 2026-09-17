from contextlib import contextmanager

import pytest

from roundtable.backend import (
    BackendCapabilities,
    RunResult,
    backend_capabilities,
    register_backend,
)


def test_external_backend_registration_includes_capabilities_without_engine_edit() -> None:
    @contextmanager
    def provider(_options):
        yield lambda _request: RunResult("ok", 0, 1, 0)

    register_backend(
        "test_contract_backend",
        provider,
        lambda: BackendCapabilities(structured_output=False, tools=False),
    )

    capabilities = backend_capabilities("test_contract_backend")
    assert capabilities.structured_output is False
    assert capabilities.tools is False
    with pytest.raises(ValueError, match="already registered"):
        register_backend("test_contract_backend", provider)

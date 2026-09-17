"""Unit tests for the SDK optional-dependency guard + version surface."""

from __future__ import annotations

import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

from roundtable.backend.sdk.compat import (
    PINNED_SDK_VERSION,
    SdkUnavailableError,
    SdkVersions,
    require_sdk,
    require_terminal_tool_support,
    sdk_versions,
)


def _fake_copilot(version: str, protocol: str) -> ModuleType:
    mod = ModuleType("copilot")
    mod.__version__ = version  # type: ignore[attr-defined]
    mod._sdk_protocol_version = protocol  # type: ignore[attr-defined]
    return mod


def test_pin_is_exact() -> None:
    assert PINNED_SDK_VERSION == "1.0.10rc1"


def test_require_sdk_missing_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # `None` in sys.modules makes `import copilot` raise ImportError.
    monkeypatch.setitem(sys.modules, "copilot", None)
    with pytest.raises(SdkUnavailableError) as exc:
        require_sdk()
    assert "pip install --force-reinstall roundtable" in str(exc.value)


def test_require_sdk_returns_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_copilot("1.0.10rc1", "2024-11")
    monkeypatch.setitem(sys.modules, "copilot", fake)
    assert require_sdk() is fake


def test_terminal_tool_support_is_required_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "copilot", _fake_copilot("1.0.10rc1", "2024-11"))
    with pytest.raises(SdkUnavailableError, match="terminal custom-tool support"):
        require_terminal_tool_support()


def test_sdk_versions_reads_module_attrs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "copilot", _fake_copilot("1.0.10rc1", "2024-11"))
    versions = sdk_versions()
    assert versions == SdkVersions(package="1.0.10rc1", protocol="2024-11")
    assert versions.matches_pin is True


def test_sdk_versions_flags_pin_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "copilot", _fake_copilot("1.0.9", "2024-11"))
    assert sdk_versions().matches_pin is False


def test_sdk_versions_tolerates_missing_attrs(monkeypatch: pytest.MonkeyPatch) -> None:
    bare = ModuleType("copilot")
    monkeypatch.setitem(sys.modules, "copilot", bare)
    versions = sdk_versions()
    assert versions == SdkVersions(package="unknown", protocol="unknown")


def test_sdk_versions_reads_protocol_module_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_copilot("1.0.10rc1", "unused")
    protocol = ModuleType("copilot._sdk_protocol_version")
    protocol.SDK_PROTOCOL_VERSION = 3  # type: ignore[attr-defined]
    fake._sdk_protocol_version = protocol  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot", fake)

    assert sdk_versions().protocol == "3"


def test_pinned_sdk_exposes_session_usage_billing_contract() -> None:
    from copilot.generated.rpc import UsageGetMetricsResult
    from copilot.session import CopilotSession

    session = CopilotSession("contract-test", SimpleNamespace())
    signature = inspect.signature(session.rpc.usage.get_metrics)
    assert "timeout" in signature.parameters
    assert {"total_nano_aiu", "total_premium_request_cost"} <= set(
        UsageGetMetricsResult.__dataclass_fields__
    )

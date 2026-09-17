"""Typed backend protocol and provider registry."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .result import RunRequest, RunResult

if TYPE_CHECKING:
    from roundtable.graph import Configuration


class Backend(Protocol):
    def run(self, request: RunRequest) -> RunResult: ...


@dataclass(frozen=True)
class BackendCapabilities:
    structured_output: bool = True
    tools: bool = True
    cancellation: bool = False
    usage: bool = True
    cost: bool = False
    diagnostics: bool = True
    provider_tool_policy: bool = False


@dataclass(frozen=True)
class BackendOptions:
    configuration: Configuration
    cwd: str | None = None
    changed_files: tuple[str, ...] = ()
    mcp_servers_by_key: Mapping[str, dict[str, dict] | None] | None = None
    env: Mapping[str, str] | None = None
    allow_logged_in_user: bool = True


BackendProvider = Callable[[BackendOptions], AbstractContextManager[Backend]]
BackendCapabilityResolver = Callable[[], BackendCapabilities]
_BACKENDS: dict[str, BackendProvider] = {}
_CAPABILITIES: dict[str, BackendCapabilityResolver] = {}


def register_backend(
    name: str,
    provider: BackendProvider,
    capabilities: BackendCapabilityResolver | None = None,
) -> None:
    if name in _BACKENDS:
        raise ValueError(f"backend already registered: {name!r}")
    _BACKENDS[name] = provider
    _CAPABILITIES[name] = capabilities or BackendCapabilities


def open_backend(name: str, options: BackendOptions) -> AbstractContextManager[Backend]:
    try:
        provider = _BACKENDS[name]
    except KeyError:
        known = ", ".join(sorted(_BACKENDS))
        raise ValueError(f"unknown backend {name!r} (known: {known})") from None
    return provider(options)


def backend_provider(name: str) -> BackendProvider:
    try:
        return _BACKENDS[name]
    except KeyError:
        known = ", ".join(sorted(_BACKENDS))
        raise ValueError(f"unknown backend {name!r} (known: {known})") from None


def backend_capabilities(name: str) -> BackendCapabilities:
    backend_provider(name)
    return _CAPABILITIES[name]()


def normalize_backend_result(request: RunRequest, result: RunResult) -> RunResult:
    """Apply the schema submission contract at the common backend boundary."""

    submission = request.submission
    if submission is None:
        return result
    final_content = getattr(result, "final_content", "")
    if not getattr(result, "raw_stdout", "") and final_content:
        result.raw_stdout = final_content
    result.submission = submission.snapshot()
    result.final_content = submission.canonical_output() if result.submission.accepted else ""
    return result


class FunctionBackend:
    def __init__(self, run: Callable[[RunRequest], RunResult]) -> None:
        self._run = run

    def run(self, request: RunRequest) -> RunResult:
        return normalize_backend_result(request, self._run(request))


class KeywordBackend:
    def __init__(self, run: Callable[..., RunResult]) -> None:
        self._run = run

    def run(self, request: RunRequest) -> RunResult:
        kwargs = {
            "agent": request.agent,
            "prompt": request.prompt,
            "add_dirs": list(request.add_dirs) or None,
            "timeout_s": request.timeout_s,
            "session_id": request.session_id,
            "cwd": request.cwd,
        }
        if request.submission is not None:
            kwargs["submission"] = request.submission
        return normalize_backend_result(request, self._run(**kwargs))


@contextmanager
def backend_context(backend: Backend) -> Iterator[Backend]:
    yield backend

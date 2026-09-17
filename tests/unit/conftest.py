from __future__ import annotations

import subprocess
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _forbid_real_external_calls(monkeypatch):
    """Unit tests must replace network and Copilot boundaries explicitly."""
    import roundtable.backend.registry as backend_registry
    from roundtable.ado import pr_iterations, pr_labels, pr_threads

    def blocked(*_args, **_kwargs):
        raise RuntimeError("unit test attempted real external I/O")

    @contextmanager
    def blocked_copilot(_options):
        blocked()
        yield

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(pr_iterations, "ado_auth_header", blocked)
    monkeypatch.setattr(pr_labels, "ado_auth_header", blocked)
    monkeypatch.setattr(pr_threads, "ado_auth_header", blocked)
    monkeypatch.setitem(backend_registry._BACKENDS, "copilot", blocked_copilot)

    real_run = subprocess.run
    real_popen = subprocess.Popen

    def executable(command) -> str:
        first = command[0] if isinstance(command, list | tuple) else str(command).split()[0]
        return Path(str(first)).stem.lower()

    def guarded_run(command, *args, **kwargs):
        if executable(command) in {"az", "copilot", "npm", "npx"}:
            return blocked()
        return real_run(command, *args, **kwargs)

    def guarded_popen(command, *args, **kwargs):
        if executable(command) in {"az", "copilot", "npm", "npx"}:
            return blocked()
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)

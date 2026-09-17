from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import eval_redgreen


@pytest.mark.parametrize("input_kind", ["session", "payload", "text"])
def test_redgreen_eval_dry_run_hashes_each_frozen_input_form(tmp_path, input_kind) -> None:
    context = "Review this exact frozen test target.\n"
    if input_kind == "text":
        raw = context.encode()
        frozen = tmp_path / "review.txt"
        frozen.write_bytes(raw)
    else:
        raw = json.dumps({"ReviewDiff": context, "GitHistory": "ignored"}).encode()
        payload = tmp_path / "source-payloads.json"
        payload.write_bytes(raw)
        if input_kind == "session":
            frozen = tmp_path / "session"
            frozen.mkdir()
            payload.replace(frozen / payload.name)
        else:
            frozen = payload
    completed = subprocess.run(
        [sys.executable, "scripts/eval_redgreen.py", "--input", str(frozen), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )
    request = json.loads(completed.stdout)
    assert request["agent"] == "redgreen"
    assert request["inputSha256"] == hashlib.sha256(raw).hexdigest()
    assert request["inputBytes"] == len(raw)
    assert request["contextSha256"] == hashlib.sha256(context.encode()).hexdigest()
    assert request["contextBytes"] == len(context.encode())
    assert request["timeoutSeconds"] == 720.0


def test_redgreen_eval_invokes_only_redgreen_directly(tmp_path, monkeypatch, capsys) -> None:
    frozen = tmp_path / "review.txt"
    frozen.write_text("frozen review", encoding="utf-8")
    calls = []

    @contextmanager
    def fake_sdk_backend(options):
        yield "backend"

    def fake_run_agent_with_ovg(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            valid=True,
            attempts=1,
            last_error="",
            response='{"findings":[]}',
        )

    monkeypatch.setattr("roundtable.backend.sdk_backend", fake_sdk_backend)
    monkeypatch.setattr("roundtable.engine.run_agent_with_ovg", fake_run_agent_with_ovg)

    assert eval_redgreen.main(["--input", str(frozen), "--cwd", str(tmp_path)]) == 0
    assert len(calls) == 1
    assert calls[0]["agent"] == "redgreen"
    assert calls[0]["backend"] == "backend"
    assert json.loads(capsys.readouterr().out)["valid"] is True

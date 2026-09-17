"""persistence.prompt_dump: opt-in per-agent prompt dump for T0 parity.

Behind the ``--dump-prompts`` flag, after a review completes this writes — per
agent — the deterministic prompt surfaces in a layout the ``verify_live_run.py``
checker (and a human) can inspect:

    <session_dir>/agents/<AgentKey>/
        system.md      — composed system prompt (body + sharedContext + mcp_usage +
                         ## Output contract)
        context.md     — the attempt-1 payload (session header + ## Git Context +
                         dossier; retries also append gate-error feedback)
        response.md    — the agent's final (canonicalised) response
        manifest.json  — slim run metadata

Only the **final attempt's** response is dumped (the slim-artifacts policy of the
port — reduction #3); ``context.md`` is the *first* attempt's payload because that
is the deterministic context T0 compares (retries append a non-deterministic
"PREVIOUS ATTEMPT OUTPUT" echo). Writes are atomic (AUDIT-011), reusing
``trace.write_atomic``.

This is a pure dumper: it never runs the model and never affects the verdict or
exit code. A failure to dump one agent is logged and skipped — a debug dump must
not fail a review.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .trace import write_atomic


def _g(outcome: Any, attr: str, default: Any) -> Any:
    if isinstance(outcome, Mapping):
        return outcome.get(attr, default)
    return getattr(outcome, attr, default)


def _agent_manifest(key: str, outcome: Any) -> dict[str, Any]:
    return {
        "agent": key,
        "attempts": int(_g(outcome, "attempts", 0) or 0),
        "valid": bool(_g(outcome, "valid", False)),
        "gate": _g(outcome, "gate", None),
        "submissionStatus": _g(outcome, "submission_status", None),
        "model": _g(outcome, "model", None),
        "toolsUsed": list(_g(outcome, "tools_used", []) or []),
    }


def dump_session_prompts(
    session_dir: Path,
    *,
    agent_outcomes: Mapping[str, Any],
    system_prompts: Mapping[str, str],
) -> Path:
    """Write the per-agent prompt dump under ``<session_dir>/agents/``.

    ``system_prompts`` maps agent key → composed system prompt (see
    ``agent_setup.system_prompts_by_key``); a missing key dumps an empty
    ``system.md`` rather than failing. Returns the ``agents/`` directory.
    """
    agents_dir = session_dir / "agents"
    for key, outcome in agent_outcomes.items():
        try:
            context_text = _g(outcome, "first_prompt", "") or ""
            response_text = _g(outcome, "response", "") or ""
            agent_dir = agents_dir / key
            agent_dir.mkdir(parents=True, exist_ok=True)
            write_atomic(agent_dir / "system.md", system_prompts.get(key, ""))
            write_atomic(agent_dir / "context.md", context_text)
            write_atomic(agent_dir / "response.md", response_text)
            write_atomic(
                agent_dir / "manifest.json",
                json.dumps(
                    _agent_manifest(key, outcome),
                    indent=2,
                    ensure_ascii=False,
                ),
            )
        except OSError as err:  # pragma: no cover — best-effort dump
            print(f"[prompt_dump] {key}: dump failed ({err}); skipping", file=sys.stderr)
    return agents_dir

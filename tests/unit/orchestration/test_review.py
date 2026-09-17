"""Unit test for review.flow.run_review (end-to-end, fake runtime)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from roundtable.bundle import resolve_bundle
from roundtable.graph import get_configuration
from roundtable.review.flow import make_session_id
from roundtable.review.flow import run_review as _run_review

_CONFIG = get_configuration(resolve_bundle("inspectorx"))
run_review = partial(_run_review, config=_CONFIG)


@dataclass
class _FakeResult:
    """Minimal stand-in for CopilotResult (only the fields agent_runner reads)."""

    final_content: str
    exit_code: int = 0
    timed_out: bool = False
    tool_call_count: int = 0
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and bool(self.final_content)


def _fake_result(content: str) -> _FakeResult:
    return _FakeResult(final_content=content)


def _make_run_fn(judge_verdict: str):
    """A fake run_fn: Judge returns a fully valid overlay; others return empty findings."""

    def run_fn(*, agent: str, prompt: str, **_kw) -> _FakeResult:
        if agent == "Judge":
            output = {
                "verdict": judge_verdict,
                "executive_summary": "s",
                "architectural_assessment": "a",
                "plan_compliance": "p",
                "verdict_overlay": [],
                "validated_safe": [],
                "needs_human_judgment": [],
                "count_verification": {},
            }
        else:
            output = {"findings": []}
        submission = _kw.get("submission")
        if submission is not None:
            submission.submit(output)
        return _fake_result(json.dumps(output))

    return run_fn


def test_make_session_id_is_filesystem_safe() -> None:
    sid = make_session_id("users/private-user/My Feature!")
    assert sid.startswith("session_")
    assert "/" not in sid and " " not in sid and "!" not in sid


def test_run_review_persists_and_returns_exit_code(tmp_path: Path) -> None:
    result = run_review(
        source_payloads={"ReviewDiff": "REVIEW CONTEXT: tiny diff"},
        label="dev/test",
        run_fn=_make_run_fn("REJECT"),
        base_dir=tmp_path,
        concurrency=2,
    )
    # REJECT ⇒ FINDINGS(1); artifacts written under tmp_path/<session_id>/.
    assert result.exit_code == 1
    assert result.verdict.verdict == "REJECT"
    assert result.persist.trace_path.exists()
    assert result.persist.report_path.exists()

    trace = json.loads(result.persist.trace_path.read_text(encoding="utf-8"))
    assert trace["verdict"] == "REJECT"
    assert trace["agentCount"] == len(result.scheduler.results)
    # Judge ran and is recorded.
    assert any(a["agent"] == "Judge" for a in trace["agents"])


def test_run_review_approve_exit_zero(tmp_path: Path) -> None:
    result = run_review(
        source_payloads={"ReviewDiff": "ctx"},
        label="clean",
        run_fn=_make_run_fn("APPROVE"),
        base_dir=tmp_path,
    )
    assert result.verdict.verdict == "APPROVE"
    assert result.exit_code == 0


def test_dump_prompts_writes_per_agent_artifacts(tmp_path: Path) -> None:
    result = run_review(
        source_payloads={"ReviewDiff": "=== Repository: demo ===\ndiff --git a/x b/x\n+added\n"},
        label="dump",
        session_header="## Change Under Review\nHDR\n\n---\n\n",
        run_fn=_make_run_fn("APPROVE"),
        base_dir=tmp_path,
        dump_prompts=True,
    )
    agents_dir = result.persist.session_dir / "agents"
    assert agents_dir.is_dir()
    judge = agents_dir / "Judge"
    # Every deterministic artifact is present.
    for fname in ("system.md", "context.md", "response.md", "manifest.json"):
        assert (judge / fname).exists(), fname
    # context.md carries the session header + the rendered ## Git Context section.
    ctx = (judge / "context.md").read_text(encoding="utf-8")
    assert ctx.startswith("## Change Under Review")
    assert "## Git Context" in ctx
    # system.md is the composed Judge system prompt (resolved from the bundle).
    assert (judge / "system.md").read_text(encoding="utf-8").strip() != ""


def test_run_review_threads_max_attempts_to_runner(tmp_path: Path, monkeypatch) -> None:
    # The retry budget must reach run_agent_with_ovg for *every* llm agent. Patch
    # the runner at the node handler's call site and assert each invocation carries
    # the value run_review was given (proving review -> scheduler -> node -> runner).
    from roundtable.engine import nodes
    from roundtable.engine.agent_runner import AgentRunOutcome

    recorded: list[int] = []

    def _fake_run_agent_with_ovg(*, agent: str, max_attempts: int, **_kw) -> AgentRunOutcome:
        recorded.append(max_attempts)
        if agent == "Judge":
            resp = json.dumps(
                {
                    "verdict": "APPROVE",
                    "executive_summary": "s",
                    "architectural_assessment": "a",
                    "plan_compliance": "p",
                    "verdict_overlay": [],
                    "validated_safe": [],
                    "needs_human_judgment": [],
                    "count_verification": {},
                }
            )
        else:
            resp = json.dumps({"findings": []})
        return AgentRunOutcome(agent=agent, response=resp, valid=True, gate=None, attempts=1)

    monkeypatch.setattr(nodes, "run_agent_with_ovg", _fake_run_agent_with_ovg)

    run_review(
        source_payloads={"ReviewDiff": "ctx"}, label="budget", base_dir=tmp_path, max_attempts=2
    )

    assert recorded, "no llm agent invoked run_agent_with_ovg"
    assert set(recorded) == {2}, f"every agent must get max_attempts=2, saw {sorted(set(recorded))}"


class _FakeOutcome:
    """Stand-in for AgentRunOutcome (only the fields _read_domain_result reads)."""

    def __init__(self, response: str, valid: bool) -> None:
        self.response = response
        self.valid = valid


def _judge_shaped_approve() -> str:
    return json.dumps(
        {
            "verdict": "APPROVE",
            "executive_summary": "s",
            "architectural_assessment": "a",
            "plan_compliance": "p",
            "verdict_overlay": [],
            "validated_safe": [],
            "needs_human_judgment": [],
            "count_verification": {},
        }
    )


def test_read_domain_result_degrades_to_unknown_without_reading_agent_shapes() -> None:
    """A dead sink must yield UNKNOWN + a reason, never a rescue from an agent payload.

    Recomputing here would hardcode ONE config's terminal-agent shape into shared
    orchestration: that config gets a silent rescue, every other config gets a
    silently wrong verdict. The Judge-shaped APPROVE below is the bait.
    """
    from roundtable.review.flow import _read_domain_result

    for sink_state, expected in (
        ({}, "No runnable sink"),
        ({"Verdict": _FakeOutcome("", True)}, "no valid response"),
        ({"Verdict": _FakeOutcome("{...", True)}, "malformed"),
    ):
        results = {"Judge": _FakeOutcome(_judge_shaped_approve(), True), **sink_state}
        verdict, counts = _read_domain_result(
            results,
            "session_20250101000000_x",
            _CONFIG,
        )
        assert verdict.verdict == "UNKNOWN", f"{sink_state} rescued a verdict from an agent payload"
        assert expected in verdict.reason, verdict.reason
        assert counts is None


def test_read_domain_result_never_recomputes_a_verdict() -> None:
    """Structural guard: the shared reader must not call any verdict computation."""
    import ast
    import inspect

    from roundtable.review import flow as review

    src = inspect.getsource(review._read_domain_result)
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(ast.parse(src.lstrip()))
        if isinstance(node, ast.Call)
    }
    assert "compute_verdict" not in called
    assert "compute_verdict" not in dir(review)


def test_run_review_forwards_concurrency_to_the_executor(monkeypatch, tmp_path: Path) -> None:
    """The last hop: run_review -> executor.run(concurrency=...).

    Closes the chain flag/env/file -> ReviewConfig -> run_review -> executor -> pool.
    Each hop is asserted somewhere; an unasserted hop is how ``timeout_seconds`` stayed
    inert while looking configured.
    """
    import roundtable.review.flow as rv

    seen: dict = {}
    real_engine = rv.Engine

    def spy_engine(backend, *, options):
        seen["concurrency"] = options.concurrency
        return real_engine(backend, options=options)

    monkeypatch.setattr(rv, "Engine", spy_engine)
    run_review(
        label="t",
        run_fn=_make_run_fn("APPROVE"),
        base_dir=tmp_path,
        concurrency=3,
    )
    assert seen["concurrency"] == 3

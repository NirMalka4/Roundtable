"""``review --publish`` — the chained review→publish path.

``review --publish`` reviews a PR and, on success, immediately posts the findings
via the *same* publish machinery the standalone ``publish`` command uses
(``_run_publish_for_session``). These tests pin the three pieces that are unique
to the chained path — none of which the standalone ``publish`` tests cover:

* the ``_chained_publish`` guards (PR required; dry-run/simulate never post) and
  its delegation shape (reviewed PR, no ``--pr`` override, forwarded options);
* ``_cmd_review``'s exit-code combiner (an operational publish failure dominates
  the review verdict; a clean publish leaves the verdict exit code intact);
* ``_run_publish_for_session``'s dispatch to the configured sink.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from roundtable import cli
from roundtable.ado.publish_flow import PublishOptions
from roundtable.decision.verdict import EXIT_ABORTED, EXIT_CLEAN, EXIT_ERROR, EXIT_FINDINGS


# ── _chained_publish: guards + delegation ────────────────────────────────────
def _review_args(**over) -> SimpleNamespace:
    base = {
        "pr": "123",
        "dry_run": False,
        "simulate": False,
        "publish": True,
        "publish_min_severity": "medium",
        "publish_dry_run": False,
        "publish_out": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_chained_publish_requires_pr(monkeypatch, capsys) -> None:
    called: list = []
    monkeypatch.setattr(cli, "_run_publish_for_session", lambda *a, **k: called.append((a, k)))

    rc = cli._chained_publish(_review_args(pr=None), Path("/sess"))

    assert rc == EXIT_CLEAN
    assert called == [], "no PR ⇒ must not attempt to publish"
    assert "requires a PR review" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["dry_run", "simulate"])
def test_chained_publish_skips_non_live_review(monkeypatch, capsys, mode: str) -> None:
    called: list = []
    monkeypatch.setattr(cli, "_run_publish_for_session", lambda *a, **k: called.append((a, k)))

    rc = cli._chained_publish(_review_args(**{mode: True}), Path("/sess"))

    assert rc == EXIT_CLEAN
    assert called == [], "no live review ran ⇒ must never post fabricated/absent findings"
    assert "no live review ran" in capsys.readouterr().err


def test_chained_publish_delegates_to_reviewed_pr(monkeypatch) -> None:
    captured: dict = {}

    def _fake(session_dir, options):
        captured.update(session_dir=session_dir, options=options)
        return EXIT_CLEAN

    monkeypatch.setattr(cli, "_run_publish_for_session", _fake)

    rc = cli._chained_publish(
        _review_args(publish_min_severity="high", publish_dry_run=True),
        Path("/sess/dir"),
    )

    assert rc == EXIT_CLEAN
    assert captured["session_dir"] == str(Path("/sess/dir"))
    opts: PublishOptions = captured["options"]
    assert opts.min_severity == "high"
    assert opts.dry_run is True
    assert opts.pr_override is None, "chained publish always targets the reviewed PR"


# ── _cmd_review: exit-code combiner ──────────────────────────────────────────
def _stub_review_to_publish(monkeypatch, tmp_path: Path, *, verdict_exit: int) -> None:
    """Stub every collaborator so ``_cmd_review`` runs a live (non-simulate,
    non-dry-run) review all the way to the ``--publish`` leg, returning a result
    whose ``exit_code`` is ``verdict_exit``."""
    repo = SimpleNamespace(name="R", branch="feat", remote_url="")
    monkeypatch.setattr(
        cli,
        "_resolve_runtime_preflight_or_report",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(cli, "detect_repos", lambda _p: [repo])
    workspace = SimpleNamespace(
        path=tmp_path,
        mode="local-worktree",
        source_sha="s" * 40,
        base_sha="b" * 40,
        cleanup=lambda: None,
    )
    target = cli._ReviewTarget(
        workspace=workspace,
        repo=repo,
        pr=SimpleNamespace(pr_id=123),
        metadata=SimpleNamespace(source_branch="feat"),
        label="feat",
        base_dir=tmp_path,
        session_id="sid",
    )
    monkeypatch.setattr(cli, "_resolve_review_target", lambda _args, _root: target)
    monkeypatch.setattr(
        cli,
        "_build_review_context",
        lambda *a, **k: (
            "CONTEXT",
            "## H\n",
            SimpleNamespace(ado_org=None),
            SimpleNamespace(),
            ["x"],
            [],
            {
                "mode": "pr",
                "sourceSha": "s" * 40,
                "baseSha": "b" * 40,
                "sourceBranch": "feat",
                "targetBranch": "main",
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "capture_replay_context",
        lambda **_kwargs: SimpleNamespace(to_dict=lambda: {"version": 1}),
    )
    from roundtable.runtime.agent_setup import ValidationReport

    monkeypatch.setattr(cli, "validate_agents", lambda *a, **k: ValidationReport())
    monkeypatch.setattr(cli, "load_mcp_needs_map", lambda *a, **k: {})
    monkeypatch.setattr(cli, "resolve_mcp_config", lambda *a, **k: None)

    def _fake_run_review(**kwargs):
        return SimpleNamespace(
            persist=SimpleNamespace(
                session_dir=tmp_path / "session",
                report_path=tmp_path / "session" / "verdict.md",
            ),
            verdict=SimpleNamespace(verdict="REJECT", verdict_icon="❌"),
            exit_code=verdict_exit,
        )

    monkeypatch.setattr(cli, "run_review", _fake_run_review)


def _live_review_args(tmp_path: Path, **over) -> SimpleNamespace:
    base = {
        "repo": str(tmp_path),
        "pr": "123",
        "base_branch": None,
        "artifacts_dir": str(tmp_path),
        "dry_run": False,
        "simulate": False,
        "dump_prompts": False,
        "session_reuse": True,
        "max_attempts": 3,
        "publish": True,
        "publish_min_severity": "medium",
        "publish_dry_run": False,
        "publish_out": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("publish_rc", "verdict_exit", "expected"),
    [
        (EXIT_ERROR, EXIT_CLEAN, EXIT_ERROR),  # failed post dominates a clean verdict
        (EXIT_ABORTED, EXIT_FINDINGS, EXIT_ABORTED),  # refused publish dominates findings
        (EXIT_CLEAN, EXIT_FINDINGS, EXIT_FINDINGS),  # clean publish ⇒ keep the review verdict
        (EXIT_CLEAN, EXIT_CLEAN, EXIT_CLEAN),
    ],
)
def test_review_publish_exit_precedence(
    monkeypatch, tmp_path: Path, publish_rc: int, verdict_exit: int, expected: int
) -> None:
    _stub_review_to_publish(monkeypatch, tmp_path, verdict_exit=verdict_exit)
    monkeypatch.setattr(cli, "_chained_publish", lambda _args, _dir: publish_rc)

    rc = cli._cmd_review(_live_review_args(tmp_path))

    assert rc == expected


def test_review_without_publish_flag_never_publishes(monkeypatch, tmp_path: Path) -> None:
    _stub_review_to_publish(monkeypatch, tmp_path, verdict_exit=EXIT_FINDINGS)
    called: list = []
    monkeypatch.setattr(cli, "_chained_publish", lambda *a: called.append(a))

    rc = cli._cmd_review(_live_review_args(tmp_path, publish=False))

    assert rc == EXIT_FINDINGS
    assert called == [], "--publish absent ⇒ the publish leg must not run"


def test_recording_failure_warns_without_returning_an_exit_code(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import roundtable.ado as ado
    from roundtable.inputs import PrReference

    result = SimpleNamespace(persist=SimpleNamespace(session_dir=tmp_path))
    pr = PrReference("o", "p", "r", 1)
    monkeypatch.setattr(ado, "build_review_record", lambda *_a, **_k: object())
    monkeypatch.setattr(
        ado,
        "record_review",
        lambda *_a, **_k: SimpleNamespace(ok=False, summary=None, label_action="failed"),
    )

    assert cli._record_pr_adoption(result, pr, {}, SimpleNamespace()) is None
    assert "verdict is unchanged" in capsys.readouterr().err


def test_branch_recording_guard_performs_no_side_effect(monkeypatch, tmp_path: Path) -> None:
    from roundtable.ado import review_record

    monkeypatch.setattr(
        review_record,
        "build_review_record",
        lambda *_a, **_k: pytest.fail("branch review must not record"),
    )

    assert (
        cli._record_pr_adoption(
            SimpleNamespace(persist=SimpleNamespace(session_dir=tmp_path)),
            None,
            {},
            SimpleNamespace(),
        )
        is None
    )


# ── _run_publish_for_session: dispatch to the configured sink ────────────────
def test_publish_helper_dispatches_to_sink(monkeypatch, tmp_path: Path) -> None:
    """``_run_publish_for_session`` dispatches straight to the configured sink and
    returns its exit code (no pre-gate)."""
    from roundtable.delivery.sink import SinkOutcome

    seen: dict = {}

    class _FakePublisher:
        name = "fake"

        def publish(self, request):
            seen["ran"] = True
            return SinkOutcome(ok=True, exit_code=EXIT_CLEAN)

        def retract(self, request):  # pragma: no cover - unused here
            return SinkOutcome(ok=True, exit_code=EXIT_CLEAN)

    monkeypatch.setattr(
        "roundtable.delivery.get_publisher",
        lambda *_a, **_k: _FakePublisher(),
    )
    (tmp_path / "graph.json").write_text(
        '{"displayName": "buddies", "agents": []}', encoding="utf-8"
    )

    rc = cli._run_publish_for_session(str(tmp_path), PublishOptions())

    assert rc == EXIT_CLEAN
    assert seen.get("ran") is True


@pytest.mark.parametrize("operation", ["publish", "unpublish"])
def test_artifact_operation_delegates_non_ambient_session_from_default_process(
    monkeypatch, tmp_path: Path, operation: str
) -> None:
    """A non-ambient session runs in a fresh process rooted at its shipped bundle."""
    import json

    from roundtable.bundle import paths

    (tmp_path / "configuration.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "shipped",
                "bundle": "inspectorx",
                "name": "inspectorx",
                "graphConfigSha": "abc",
            }
        ),
        encoding="utf-8",
    )
    seen: dict = {}

    def _run(command, *, env, check):
        seen.update(command=command, env=env, check=check)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", _run)
    options = (
        PublishOptions(allow_config_drift=True)
        if operation == "publish"
        else cli.UnpublishOptions()
    )

    rc = cli._delegate_artifact_operation(operation, str(tmp_path), options)

    assert rc == EXIT_CLEAN
    assert seen["command"][2:4] == ["roundtable.artifact_worker", operation]
    assert Path(seen["env"][paths.ENV_VAR]).name == "inspectorx"
    assert seen["check"] is False
    if operation == "publish":
        assert json.loads(seen["command"][5])["allow_config_drift"] is True


def test_publish_fingerprint_drift_blocks_before_sink(monkeypatch, tmp_path, capsys) -> None:
    from roundtable.bundle import paths
    from roundtable.graph.session_identity import ConfigurationIdentity

    identity = ConfigurationIdentity(paths.config_root().resolve(), "inspectorx", "recorded")
    monkeypatch.setattr(
        "roundtable.graph.resolve_session_configuration",
        lambda _session: identity,
    )
    monkeypatch.setattr("roundtable.graph.session_identity.graph_config_sha", lambda *_: "current")

    rc = cli._delegate_artifact_operation("publish", str(tmp_path), PublishOptions())

    assert rc != EXIT_CLEAN
    assert "recorded=recorded, current=current" in capsys.readouterr().err


def test_unpublish_fingerprint_drift_warns_but_remains_retractable(
    monkeypatch, tmp_path, capsys
) -> None:
    from roundtable.bundle import paths
    from roundtable.graph.session_identity import ConfigurationIdentity

    identity = ConfigurationIdentity(paths.config_root().resolve(), "inspectorx", "recorded")
    monkeypatch.setattr(
        "roundtable.graph.resolve_session_configuration",
        lambda _session: identity,
    )
    monkeypatch.setattr("roundtable.graph.session_identity.graph_config_sha", lambda *_: "current")

    delegated = cli._delegate_artifact_operation("unpublish", str(tmp_path), cli.UnpublishOptions())

    assert delegated is None
    assert "continuing so the session remains retractable" in capsys.readouterr().err


def test_unresolved_session_is_blocked_before_sink_resolution(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(
        cli, "_configured_sink", lambda *_a, **_k: pytest.fail("must not use ambient sink")
    )

    rc = cli._run_publish_for_session(str(tmp_path), PublishOptions())

    assert rc == cli.EXIT_BAD_ARGS
    assert "cannot resolve session configuration" in capsys.readouterr().err

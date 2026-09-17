from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roundtable.review_skill import (
    METADATA_FILE,
    install,
    status,
    uninstall,
)

ROOT = Path(__file__).resolve().parents[3]
PROJECT_SKILL = ROOT / ".github" / "skills" / "roundtable-review"
PACKAGED_SKILL = ROOT / "roundtable" / "skills" / "roundtable-review"
PLANNER = PROJECT_SKILL / "scripts" / "plan_review.py"
BRIEFER = PROJECT_SKILL / "scripts" / "brief_verdict.py"


def _run_planner(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PLANNER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_brief(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BRIEFER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _load_briefer():
    """Import the brief script as a module so its tables can be pinned directly.

    Bytecode writing is suppressed: the skill tree is mirrored byte-for-byte into the
    package, so a stray ``__pycache__`` here would make the two copies differ.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("brief_verdict", BRIEFER)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _committed_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=path, check=True)
    return path


def test_planner_emits_exact_buddies_argv_for_every_supported_mode(tmp_path):
    repo = _committed_repo(tmp_path / "Windows Path" / "repo")
    session = tmp_path / "frozen"
    session.mkdir()
    fetch = str(repo.resolve())
    normalized_fetch = repo.resolve().as_uri().lower()
    replay_context = {
        "version": 1,
        "repository": {
            "name": "repo",
            "fetchUrl": fetch,
            "normalizedRemoteUrl": normalized_fetch,
        },
        "revision": {
            "mode": "local",
            "sourceSha": "a" * 40,
            "baseSha": "b" * 40,
            "sourceBranch": "feature",
            "baseBranch": "main",
        },
        "adoIdentities": [],
        "changedFiles": ["tracked.txt"],
        "sessionHeader": "",
        "workspacePath": str(repo),
        "hintArtifact": None,
        "workspaceOverlay": None,
    }
    (session / "replay-context.json").write_text(json.dumps(replay_context), encoding="utf-8")
    for name in ("source-payloads.json", "configuration.json", "trace.json"):
        (session / name).write_text("{}\n", encoding="utf-8")

    cases = [
        (
            ("--pr", "https://dev.azure.com/org/project/_git/repo/pullrequest/42"),
            [
                "roundtable",
                "review",
                "--pr",
                "https://dev.azure.com/org/project/_git/repo/pullrequest/42",
                "--config",
                "buddies",
            ],
        ),
        (
            (
                "--pr",
                "https://contoso.visualstudio.com/project/_git/repo/pullRequest/43",
            ),
            [
                "roundtable",
                "review",
                "--pr",
                "https://contoso.visualstudio.com/project/_git/repo/pullRequest/43",
                "--config",
                "buddies",
            ],
        ),
        (
            ("--pr", "42", "--repo", str(repo), "--publish"),
            [
                "roundtable",
                "review",
                str(repo.resolve()),
                "--pr",
                "42",
                "--publish",
                "--config",
                "buddies",
            ],
        ),
        (
            ("--repo", str(repo), "--base", "origin/main"),
            [
                "roundtable",
                "review",
                str(repo.resolve()),
                "--base-branch",
                "origin/main",
                "--config",
                "buddies",
            ],
        ),
        (
            ("--repo", str(repo)),
            [
                "roundtable",
                "review",
                str(repo.resolve()),
                "--config",
                "buddies",
            ],
        ),
        (
            ("--input-from", str(session)),
            [
                "roundtable",
                "review",
                "--input-from",
                str(session.resolve()),
                "--config",
                "buddies",
            ],
        ),
    ]

    for arguments, expected in cases:
        result = _run_planner(*arguments)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"argv": expected}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--pr", "42"), "numeric PR ID requires --repo"),
        (
            (
                "--pr",
                "https://dev.azure.com/org/project/_git/repo/pullrequest/42",
                "--base",
                "main",
            ),
            "--base cannot be combined with --pr",
        ),
        (
            (
                "--pr",
                "https://dev.azure.com/org/project/_git/repo/pullrequest/42",
                "--input-from",
                "session",
            ),
            "choose exactly one input",
        ),
        (("--input-from", "missing"), "incomplete replay session"),
        (("--input-from", "missing", "--publish"), "incomplete replay session"),
        (("--file", "roundtable/cli.py"), "static file scope is unsupported"),
        (("--module", "roundtable.cli"), "static module scope is unsupported"),
        (("--function", "roundtable.cli.main"), "static function scope is unsupported"),
    ],
)
def test_planner_rejects_unsupported_or_incomplete_requests(arguments, message):
    result = _run_planner(*arguments)

    assert result.returncode == 2
    assert message in result.stderr
    assert result.stdout == ""


def test_planner_rejects_dirty_repo_and_non_pr_publish(tmp_path):
    repo = _committed_repo(tmp_path / "repo")
    dirty = repo / "tracked.txt"
    dirty.write_text("dirty\n", encoding="utf-8")

    result = _run_planner("--repo", str(repo), "--base", "main")
    assert result.returncode == 2
    assert "repository is dirty; commit the intended change first" in result.stderr

    subprocess.run(["git", "checkout", "--", str(dirty)], cwd=repo, check=True)
    result = _run_planner("--repo", str(repo), "--base", "main", "--publish")
    assert result.returncode == 2
    assert "--publish is supported only for PR reviews" in result.stderr


def test_project_skill_and_packaged_mirror_are_byte_identical():
    project = {
        path.relative_to(PROJECT_SKILL): path.read_bytes()
        for path in PROJECT_SKILL.rglob("*")
        if path.is_file()
    }
    packaged = {
        path.relative_to(PACKAGED_SKILL): path.read_bytes()
        for path in PACKAGED_SKILL.rglob("*")
        if path.is_file()
    }

    assert packaged == project


def _claim(
    claim_id: str,
    disposition: str,
    severity: str,
) -> dict:
    return {
        "id": claim_id,
        "source_finding_ids": ["reviewer::F-01"],
        "primary_source_finding_id": "reviewer::F-01",
        "title": f"{claim_id} title",
        "criterion": "functional_reliability",
        "disposition": disposition,
        "severity": severity,
        "reason": f"{claim_id} reason",
        "evidence": ["src/code.py:10 -- observation"] if disposition == "upheld" else [],
    }


def _trace(claims: list[dict], *, label: str, counts: dict | None = None) -> dict:
    """A session trace shaped like a real one: the run's derived verdict, then the Judge."""
    payload = {"verdict": {"summary": "A blocker remains."}, "claims": claims}
    return {
        "verdict": label,
        "counts": counts or {"blocking": 1, "nonBlocking": 3, "all": 4, "security": 0},
        "agents": [{"agent": "Judge", "response": json.dumps(payload), "valid": True}],
    }


def test_brief_verdict_maps_claims_to_action_guidance(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    trace = _trace(
        [
            _claim("J-01", "upheld", "high"),
            _claim("J-02", "upheld", "medium"),
            _claim("J-03", "upheld", "low"),
            _claim("J-04", "insufficient_evidence", "medium"),
            _claim("J-05", "rejected", "none"),
        ],
        label="REJECT",
    )
    (session / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    result = _run_brief(str(session))

    assert result.returncode == 0, result.stderr
    brief = json.loads(result.stdout)
    assert [claim["address"] for claim in brief["claims"]] == [
        "yes",
        "yes",
        "optional",
        "investigate",
        "no",
    ]
    assert brief["claims"][0]["primarySourceFindingId"] == "reviewer::F-01"
    assert brief["warnings"] == []


def test_brief_verdict_reports_the_runs_derived_label_and_counts(tmp_path):
    """The brief never re-derives a verdict; it reports the one the run published."""
    session = tmp_path / "session"
    session.mkdir()
    counts = {"blocking": 1, "nonBlocking": 5, "all": 6, "security": 0}
    trace = _trace([_claim("J-01", "upheld", "medium")], label="REJECT", counts=counts)
    (session / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    result = _run_brief(str(session))

    assert result.returncode == 0, result.stderr
    brief = json.loads(result.stdout)
    assert brief["verdict"]["label"] == "REJECT"
    assert brief["verdict"]["counts"] == counts


@pytest.mark.parametrize(("missing", "expected"), [("verdict", "label"), ("counts", "counts")])
def test_brief_verdict_refuses_a_session_without_a_derived_verdict(tmp_path, missing, expected):
    session = tmp_path / "session"
    session.mkdir()
    trace = _trace([_claim("J-01", "upheld", "medium")], label="REJECT")
    del trace[missing]
    (session / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    result = _run_brief(str(session))

    assert result.returncode == 2
    assert f"no derived verdict {expected}" in result.stderr


def test_brief_verdict_surfaces_claim_consistency_warnings_without_re_reviewing(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    # `high` requires `upheld`, so this pair is one no configuration adjudicates.
    claim = _claim("J-01", "insufficient_evidence", "high")
    claim["evidence"] = []
    trace = _trace([claim], label="REJECT")
    (session / "trace.json").write_text(json.dumps(trace), encoding="utf-8")

    result = _run_brief(str(session))

    assert result.returncode == 0, result.stderr
    brief = json.loads(result.stdout)
    assert brief["claims"][0]["warnings"] == [
        "unrecognized adjudication insufficient_evidence/high"
    ]
    assert brief["claims"][0]["address"] == "investigate"


def test_brief_verdict_address_map_covers_exactly_the_adjudicated_pairs():
    """The brief's actionability map and the bundle's ruling matrix share one vocabulary.

    The brief cannot import a bundle, so the two are pinned here instead. This is the
    guard that fails when a claim field or an enum value is added, removed, or renamed.
    """
    from roundtable.configs.buddies.plugins.verdict import RULING_MATRIX

    briefer = _load_briefer()
    adjudicated = {
        (disposition, severity)
        for disposition, severities in RULING_MATRIX.items()
        for severity in severities
    }

    assert set(briefer.ADDRESS_BY_ADJUDICATION) == adjudicated


def test_brief_verdict_reads_no_field_the_judge_schema_forbids():
    """Every claim key the brief reads must be one the Judge schema actually defines."""
    import yaml

    schema_path = ROOT / "roundtable" / "configs" / "buddies" / "schemas" / "judge.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    defined = set(schema["properties"]["claims"]["items"]["properties"])

    briefer = _load_briefer()
    brief = briefer._claim_brief(_claim("J-01", "upheld", "medium"))
    read_keys = {
        "primary_source_finding_id" if key == "primarySourceFindingId" else key for key in brief
    }

    assert read_keys - {"address", "warnings"} <= defined


def test_brief_verdict_rejects_incomplete_session(tmp_path):
    result = _run_brief(str(tmp_path / "missing"))

    assert result.returncode == 2
    assert "could not read completed session trace" in result.stderr


def test_install_status_and_uninstall_protect_divergence(tmp_path):
    destination = tmp_path / "override" / "roundtable-review"

    assert status(destination)["status"] == "absent"
    assert install(destination)["status"] == "current"
    metadata = json.loads((destination / METADATA_FILE).read_text(encoding="utf-8"))
    assert metadata["packageVersion"]
    assert len(metadata["contentHash"]) == 64

    metadata["packageVersion"] = "0.0.0"
    (destination / METADATA_FILE).write_text(json.dumps(metadata), encoding="utf-8")
    assert status(destination)["status"] == "stale"
    assert install(destination)["status"] == "current"

    with (destination / "SKILL.md").open("a", encoding="utf-8") as handle:
        handle.write("\nlocal edit\n")
    assert status(destination)["status"] == "diverged"
    with pytest.raises(ValueError, match="diverged"):
        install(destination)
    with pytest.raises(ValueError, match="diverged"):
        uninstall(destination)

    assert uninstall(destination, force=True)["status"] == "absent"


def test_forced_uninstall_is_idempotent_when_skill_is_absent(tmp_path):
    destination = tmp_path / "missing" / "roundtable-review"

    assert uninstall(destination, force=True)["status"] == "absent"


def test_install_restores_previous_skill_if_atomic_swap_fails(tmp_path, monkeypatch):
    destination = tmp_path / "roundtable-review"
    install(destination)
    metadata_path = destination / METADATA_FILE
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["packageVersion"] = "0.0.0"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    original_replace = os.replace

    def fail_new_install(source, target):
        if (
            Path(target) == destination
            and Path(source).name.startswith(".roundtable-review-")
            and Path(source).name != ".roundtable-review-backup"
        ):
            raise OSError("deliberate swap failure")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_new_install)

    with pytest.raises(OSError, match="deliberate swap failure"):
        install(destination)
    assert status(destination)["status"] == "stale"


def test_review_skill_cli_uses_destination_override(tmp_path):
    destination = tmp_path / "cli-skill"

    states = []
    for command in ("status-review", "install-review", "status-review", "uninstall-review"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "roundtable.cli",
                "skill",
                command,
                "--destination",
                str(destination),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        states.append(json.loads(result.stdout)["status"])

    assert states == ["absent", "current", "current", "absent"]

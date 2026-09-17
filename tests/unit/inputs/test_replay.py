from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from roundtable.inputs import (
    ReplayError,
    ReplaySession,
    capture_replay_context,
    load_replay_session,
    materialize_replay_access,
    restore_replay_session,
)
from roundtable.inputs.ado_identity import AdoIdentity
from roundtable.inputs.replay import replay_context_from_dict
from roundtable.settings import WorkspaceSettings


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return result.stdout.decode().strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feature")
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "commit", "-am", "source")
    source = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", "main", "feature")
    return repo, base, source


def _identity() -> AdoIdentity:
    return AdoIdentity(
        org="org",
        project="project",
        repo_name="repo",
        remote_url="https://dev.azure.com/org/project/_git/repo",
        host="dev.azure.com",
        repository_id="repository-guid",
        project_id="project-guid",
    )


def _capture(
    repo: Path,
    base: str,
    source: str,
    *,
    overlay: bool = True,
    mode: str = "branch",
    changed_files: list[str] | None = None,
    **caps,
):
    session_dir = repo.parent / "capture-session"
    session_dir.mkdir(exist_ok=True)
    return capture_replay_context(
        repo_name="repo",
        repo_path=repo,
        remote_url=_git(repo, "remote", "get-url", "origin"),
        mode=mode,
        source_sha=source,
        base_sha=base,
        source_branch="feature",
        base_branch="main",
        ado_identities=[_identity()],
        changed_files=changed_files or ["tracked.txt"],
        session_header=f'workspace_path: "{repo}"\n',
        session_dir=session_dir,
        hint_path=None,
        hint_grant_dir=None,
        capture_overlay=overlay,
        **caps,
    )


def _session(tmp_path: Path, context, payloads: dict[str, str] | None = None) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    (session / "replay-context.json").write_text(
        json.dumps(context.to_dict()),
        encoding="utf-8",
    )
    (session / "source-payloads.json").write_text(
        json.dumps(payloads or {"ReviewDiff": "recorded diff\r\n", "GitHistory": "history\n"}),
        encoding="utf-8",
    )
    (session / "configuration.json").write_text(
        json.dumps({"graphConfigSha": "recorded"}),
        encoding="utf-8",
    )
    (session / "trace.json").write_text("{}", encoding="utf-8")
    return session


def _settings(tmp_path: Path) -> WorkspaceSettings:
    return WorkspaceSettings(
        repo_search_paths=(),
        clone_cache_dir=str(tmp_path / "cache"),
        clone_cache_max_gb=1,
        clone_cache_ttl_days=1,
    )


@pytest.mark.parametrize("mode", ["branch", "pr"])
def test_clean_replay_restores_detached_commit_payloads_and_identity(tmp_path, mode) -> None:
    repo, base, source = _repository(tmp_path)
    context = _capture(repo, base, source, overlay=False, mode=mode)
    session = _session(tmp_path, context)

    restored = restore_replay_session(
        session,
        settings=_settings(tmp_path),
        worktree_dir=tmp_path / "restored",
    )

    assert _git(restored.workspace.path, "rev-parse", "HEAD") == source
    assert _git(restored.workspace.path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert restored.session.source_payloads == {
        "ReviewDiff": "recorded diff\r\n",
        "GitHistory": "history\n",
    }
    assert restored.session.context.ado_identities == (_identity(),)
    path = restored.workspace.path
    restored.workspace.cleanup()
    assert not path.exists()


def test_dirty_overlay_restores_binary_patch_and_reviewed_untracked_files(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    (repo / "tracked.txt").write_bytes(b"dirty\0tracked\n")
    (repo / "new.bin").write_bytes(b"\x00\xffnew")
    (repo / "ignored.txt").write_text("ignored", encoding="utf-8")
    (repo / "dist").mkdir()
    (repo / "dist" / "generated.js").write_text("generated", encoding="utf-8")
    context = _capture(repo, base, source, changed_files=["tracked.txt", "new.bin"])
    session = _session(tmp_path, context)

    restored = restore_replay_session(
        session,
        settings=_settings(tmp_path),
        worktree_dir=tmp_path / "restored",
    )

    assert (restored.workspace.path / "tracked.txt").read_bytes() == b"dirty\0tracked\n"
    assert (restored.workspace.path / "new.bin").read_bytes() == b"\x00\xffnew"
    assert not (restored.workspace.path / "ignored.txt").exists()
    assert not (restored.workspace.path / "dist" / "generated.js").exists()
    assert context.workspace_overlay is not None
    assert context.workspace_overlay.aggregate_size > 0
    restored.workspace.cleanup()


def test_capture_rejects_unreviewed_untracked_files(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    (repo / "local-secret.txt").write_text("not part of the review", encoding="utf-8")

    with pytest.raises(ReplayError, match="outside the reviewed change set"):
        _capture(repo, base, source)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda raw: raw["workspaceOverlay"]["trackedPatch"].update({"sha256": "0" * 64}),
            "sha256 mismatch",
        ),
        (
            lambda raw: raw["workspaceOverlay"]["untrackedFiles"][0].update({"path": "../x"}),
            "unsafe replay path",
        ),
        (
            lambda raw: raw["workspaceOverlay"].update({"aggregateSize": 1}),
            "aggregate size mismatch",
        ),
    ],
)
def test_replay_contract_rejects_hash_path_and_size_tampering(tmp_path, mutation, message) -> None:
    repo, base, source = _repository(tmp_path)
    (repo / "new.txt").write_text("new", encoding="utf-8")
    raw = _capture(repo, base, source, changed_files=["tracked.txt", "new.txt"]).to_dict()
    mutation(raw)

    with pytest.raises(ReplayError, match=message):
        replay_context_from_dict(raw)


def test_capture_fails_instead_of_truncating_file_patch_or_aggregate(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    (repo / "new.txt").write_bytes(b"1234")
    with pytest.raises(ReplayError, match="untracked file exceeds"):
        _capture(repo, base, source, changed_files=["tracked.txt", "new.txt"], max_file_bytes=3)

    (repo / "new.txt").unlink()
    (repo / "tracked.txt").write_text("dirty content\n", encoding="utf-8")
    with pytest.raises(ReplayError, match="tracked workspace patch exceeds"):
        _capture(repo, base, source, max_patch_bytes=3)

    (repo / "new.txt").write_bytes(b"1234")
    with pytest.raises(ReplayError, match="aggregate cap"):
        _capture(
            repo,
            base,
            source,
            changed_files=["tracked.txt", "new.txt"],
            max_aggregate_bytes=4,
        )


def test_capture_rejects_untracked_symlink_without_following_it(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    target = tmp_path / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    link = repo / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks are not available")

    with pytest.raises(ReplayError, match="symlink"):
        _capture(repo, base, source, changed_files=["tracked.txt", "link.txt"])


def test_capture_sanitizes_credentials_and_preserves_ssh_username(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    _git(
        repo,
        "remote",
        "set-url",
        "origin",
        "https://user:secret@dev.azure.com/org/project/_git/repo?token=secret",
    )
    identity = AdoIdentity(
        org="org",
        project="project",
        repo_name="repo",
        remote_url="https://user:secret@dev.azure.com/org/project/_git/repo?token=secret",
        host="dev.azure.com",
    )
    context = capture_replay_context(
        repo_name="repo",
        repo_path=repo,
        remote_url=_git(repo, "remote", "get-url", "origin"),
        mode="branch",
        source_sha=source,
        base_sha=base,
        source_branch="feature",
        base_branch="main",
        ado_identities=[identity],
        changed_files=["tracked.txt"],
        session_header=f'workspace_path: "{repo}"\n',
        session_dir=repo.parent / "capture-session",
        hint_path=None,
        hint_grant_dir=None,
        capture_overlay=False,
    )

    assert context.repository.fetch_url == "https://dev.azure.com/org/project/_git/repo"
    assert context.ado_identities[0].remote_url == "https://dev.azure.com/org/project/_git/repo"

    _git(
        repo,
        "remote",
        "set-url",
        "origin",
        "ssh://git@ssh.dev.azure.com/v3/org/project/repo",
    )
    ssh_context = _capture(repo, base, source, overlay=False)
    assert ssh_context.repository.fetch_url.startswith("ssh://git@ssh.dev.azure.com/")


def test_replay_contract_rejects_credential_bearing_identity_url(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    raw = _capture(repo, base, source, overlay=False).to_dict()
    raw["providerIdentities"][0]["extensions"]["remoteUrl"] = (
        "https://user:secret@dev.azure.com/org/project/_git/repo"
    )

    with pytest.raises(ReplayError, match="must not contain credentials"):
        replay_context_from_dict(raw)


def test_replay_contract_accepts_a_legacy_ado_identity_key(tmp_path) -> None:
    """Sessions recorded before ADO hosts were collapsed must still replay."""
    repo, base, source = _repository(tmp_path)
    raw = _capture(repo, base, source, overlay=False).to_dict()
    raw["repository"]["fetchUrl"] = "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo"
    raw["repository"]["normalizedRemoteUrl"] = (
        "contoso.visualstudio.com/contoso/exampleproject/examplerepo"
    )

    context = replay_context_from_dict(raw)

    assert context.repository.normalized_remote_url == (
        "dev.azure.com/contoso/exampleproject/examplerepo"
    )


def test_replay_contract_rejects_an_identity_key_for_another_repo(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    raw = _capture(repo, base, source, overlay=False).to_dict()
    raw["repository"]["fetchUrl"] = "https://dev.azure.com/contoso/ExampleProject/_git/ExampleRepo"
    raw["repository"]["normalizedRemoteUrl"] = (
        "contoso.visualstudio.com/contoso/exampleproject/other"
    )

    with pytest.raises(ReplayError, match="normalized remote identity"):
        replay_context_from_dict(raw)


def test_replay_remaps_workspace_and_saved_hint_paths(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)

    session_dir = repo.parent / "capture-session"
    grant_dir = session_dir / "hints"
    pointer = grant_dir / "hint.txt"
    grant_dir.mkdir(parents=True)
    pointer.write_text("hint", encoding="utf-8")
    header = f'workspace_path: "{repo}"\nHint: `{pointer}`\n'
    context = capture_replay_context(
        repo_name="repo",
        repo_path=repo,
        remote_url=_git(repo, "remote", "get-url", "origin"),
        mode="branch",
        source_sha=source,
        base_sha=base,
        source_branch="feature",
        base_branch="main",
        ado_identities=[_identity()],
        changed_files=["tracked.txt"],
        session_header=header,
        session_dir=session_dir,
        hint_path=str(pointer),
        hint_grant_dir=str(grant_dir),
        capture_overlay=False,
    )
    replay = ReplaySession(session_dir, context, {"ReviewDiff": "diff"}, None)
    restored_workspace = tmp_path / "restored-workspace"
    restored_workspace.mkdir()

    remapped, grants = materialize_replay_access(replay, restored_workspace)

    assert str(restored_workspace) in remapped
    assert str(repo) not in remapped
    assert str(pointer) in remapped
    assert grants == (str(grant_dir),)


def test_restore_fails_for_missing_recorded_commit_and_cleans_workspace(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    raw = _capture(repo, base, source, overlay=False).to_dict()
    raw["revision"]["baseSha"] = "f" * 40
    context = replay_context_from_dict(raw)
    session = _session(tmp_path, context)
    worktree = tmp_path / "restored"

    with pytest.raises(ReplayError, match="base commit is unavailable"):
        restore_replay_session(
            session,
            settings=_settings(tmp_path),
            worktree_dir=worktree,
        )
    assert not worktree.exists()


def test_restore_cleans_workspace_after_overlay_application_failure(tmp_path, monkeypatch) -> None:
    import roundtable.inputs.replay as replay_module

    repo, base, source = _repository(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    context = _capture(repo, base, source)
    session = _session(tmp_path, context)
    worktree = tmp_path / "restored"

    def fail_overlay(*_args):
        raise ReplayError("overlay failed")

    monkeypatch.setattr(replay_module, "apply_workspace_overlay", fail_overlay)
    with pytest.raises(ReplayError, match="overlay failed"):
        restore_replay_session(
            session,
            settings=_settings(tmp_path),
            worktree_dir=worktree,
        )
    assert not worktree.exists()


def test_restore_cleans_workspace_after_untracked_collision(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    (repo / "new.txt").write_text("new", encoding="utf-8")
    raw = _capture(
        repo,
        base,
        source,
        changed_files=["tracked.txt", "new.txt"],
    ).to_dict()
    raw["workspaceOverlay"]["untrackedFiles"][0]["path"] = "tracked.txt"
    context = replay_context_from_dict(raw)
    session = _session(tmp_path, context)
    worktree = tmp_path / "restored"

    with pytest.raises(ReplayError, match="would overwrite repository content"):
        restore_replay_session(
            session,
            settings=_settings(tmp_path),
            worktree_dir=worktree,
        )
    assert not worktree.exists()


def test_restore_reports_remote_clone_failure(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    raw = _capture(repo, base, source, overlay=False).to_dict()
    missing = tmp_path / "missing.git"
    raw["repository"]["fetchUrl"] = str(missing)
    raw["repository"]["normalizedRemoteUrl"] = missing.resolve().as_uri().lower()
    context = replay_context_from_dict(raw)
    session = _session(tmp_path, context)

    with pytest.raises(RuntimeError, match="could not clone"):
        restore_replay_session(
            session,
            settings=_settings(tmp_path),
            worktree_dir=tmp_path / "restored",
        )


def test_loader_preserves_source_payload_string_meaning(tmp_path) -> None:
    repo, base, source = _repository(tmp_path)
    payloads = {"ReviewDiff": "a\r\nb\u0000", "GitHistory": "Ω\n"}
    session = _session(tmp_path, _capture(repo, base, source, overlay=False), payloads)

    assert load_replay_session(session).source_payloads == payloads

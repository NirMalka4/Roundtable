"""Versioned capture and restoration of an exact review workspace context."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import stat
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from roundtable.providers import ProviderId, RepositoryIdentity

from .ado_identity import AdoIdentity
from .workspace import WorkspaceRequest, build_review_workspace
from .workspace.remote_url import canonical_ado_identity, normalize_remote_url

if TYPE_CHECKING:
    from roundtable.settings import WorkspaceSettings

REPLAY_CONTEXT_VERSION = 2
REPLAY_CONTEXT_V1 = 1
REPLAY_CONTEXT_FILE = "replay-context.json"
REQUIRED_SESSION_FILES = (
    REPLAY_CONTEXT_FILE,
    "source-payloads.json",
    "configuration.json",
    "trace.json",
)
MAX_TRACKED_PATCH_BYTES = 8 * 1024 * 1024
MAX_UNTRACKED_FILE_BYTES = 1024 * 1024
MAX_OVERLAY_BYTES = 16 * 1024 * 1024

_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "bin",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "obj",
        "out",
        "target",
    }
)


class ReplayError(RuntimeError):
    """The saved replay contract is invalid or cannot be restored exactly."""


@dataclass(frozen=True)
class ReplayRepository:
    name: str
    fetch_url: str
    normalized_remote_url: str


@dataclass(frozen=True)
class ReplayRevision:
    mode: str
    source_sha: str
    base_sha: str
    source_branch: str
    base_branch: str


@dataclass(frozen=True)
class ReplayUntrackedFile:
    path: str
    size: int
    sha256: str
    content_base64: str
    executable: bool


@dataclass(frozen=True)
class ReplayWorkspaceOverlay:
    tracked_patch_base64: str
    tracked_patch_size: int
    tracked_patch_sha256: str
    untracked_files: tuple[ReplayUntrackedFile, ...]
    aggregate_size: int
    core_autocrlf: str


@dataclass(frozen=True)
class ReplayHintArtifact:
    recorded_path: str
    pointer_path: str
    grant_dir: str


@dataclass(frozen=True)
class ReplayContext:
    version: int
    repository: ReplayRepository
    revision: ReplayRevision
    ado_identities: tuple[AdoIdentity, ...]
    changed_files: tuple[str, ...]
    session_header: str
    recorded_workspace_path: str
    hint_artifact: ReplayHintArtifact | None = None
    workspace_overlay: ReplayWorkspaceOverlay | None = None
    provider_identities: tuple[RepositoryIdentity, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "repository": {
                "name": self.repository.name,
                "fetchUrl": self.repository.fetch_url,
                "normalizedRemoteUrl": self.repository.normalized_remote_url,
            },
            "revision": {
                "mode": self.revision.mode,
                "sourceSha": self.revision.source_sha,
                "baseSha": self.revision.base_sha,
                "sourceBranch": self.revision.source_branch,
                "baseBranch": self.revision.base_branch,
            },
            "changedFiles": list(self.changed_files),
            "sessionHeader": self.session_header,
            "workspacePath": self.recorded_workspace_path,
            "hintArtifact": (
                {
                    "recordedPath": self.hint_artifact.recorded_path,
                    "pointerPath": self.hint_artifact.pointer_path,
                    "grantDir": self.hint_artifact.grant_dir,
                }
                if self.hint_artifact is not None
                else None
            ),
            "workspaceOverlay": (
                {
                    "trackedPatch": {
                        "encoding": "base64",
                        "size": self.workspace_overlay.tracked_patch_size,
                        "sha256": self.workspace_overlay.tracked_patch_sha256,
                        "content": self.workspace_overlay.tracked_patch_base64,
                    },
                    "untrackedFiles": [
                        {
                            "path": item.path,
                            "size": item.size,
                            "sha256": item.sha256,
                            "content": item.content_base64,
                            "encoding": "base64",
                            "executable": item.executable,
                        }
                        for item in self.workspace_overlay.untracked_files
                    ],
                    "aggregateSize": self.workspace_overlay.aggregate_size,
                    "coreAutocrlf": self.workspace_overlay.core_autocrlf,
                }
                if self.workspace_overlay is not None
                else None
            ),
        }
        if self.version == REPLAY_CONTEXT_V1:
            payload["adoIdentities"] = [_ado_identity_to_dict(item) for item in self.ado_identities]
        else:
            payload["providerIdentities"] = [
                identity.to_dict() for identity in self.provider_identities
            ]
        return payload


def _ado_identity_to_dict(identity: AdoIdentity) -> dict[str, Any]:
    return {
        "org": identity.org,
        "project": identity.project,
        "repoName": identity.repo_name,
        "remoteUrl": identity.remote_url,
        "host": identity.host,
        "repositoryId": identity.repository_id,
        "projectId": identity.project_id,
    }


def _provider_identity(identity: AdoIdentity) -> RepositoryIdentity:
    extensions = {
        "organization": identity.org,
        "project": identity.project,
        "remoteUrl": identity.remote_url,
    }
    if identity.repository_id is not None:
        extensions["repositoryId"] = identity.repository_id
    if identity.project_id is not None:
        extensions["projectId"] = identity.project_id
    return RepositoryIdentity(
        provider=ProviderId("azure_devops"),
        host=identity.host,
        locator=f"{identity.org}/{identity.project}/{identity.repo_name}",
        display_name=identity.repo_name,
        canonical_url=normalize_remote_url(identity.remote_url) if identity.remote_url else None,
        extensions=extensions,
    )


def _ado_identity(identity: RepositoryIdentity) -> AdoIdentity | None:
    if str(identity.provider) != "azure_devops":
        return None
    extensions = identity.extensions
    parts = identity.locator.split("/", 2)
    org = str(extensions.get("organization") or (parts[0] if len(parts) == 3 else ""))
    project = str(extensions.get("project") or (parts[1] if len(parts) == 3 else ""))
    if not org or not project:
        raise ReplayError("Azure DevOps provider identity requires organization and project")
    return AdoIdentity(
        org=org,
        project=project,
        repo_name=identity.display_name,
        remote_url=str(extensions.get("remoteUrl") or ""),
        host=identity.host,
        repository_id=_extension_string(extensions, "repositoryId"),
        project_id=_extension_string(extensions, "projectId"),
    )


def _extension_string(extensions: Any, key: str) -> str | None:
    value = extensions.get(key)
    if value is not None and not isinstance(value, str):
        raise ReplayError(f"provider identity extension {key} must be a string")
    return value


@dataclass(frozen=True)
class ReplaySession:
    session_dir: Path
    context: ReplayContext
    source_payloads: dict[str, str]
    graph_config_sha: str | None


@dataclass(frozen=True)
class RestoredReplay:
    session: ReplaySession
    workspace: Any


def _git_bytes(repo: Path, args: list[str], *, input_data: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            input=input_data,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise ReplayError(f"git {' '.join(args)} failed: {err}") from err
    if proc.returncode:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise ReplayError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ReplayError(f"unsafe replay path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReplayError(f"unsafe replay path: {value!r}")
    if path.parts[0].endswith(":"):
        raise ReplayError(f"unsafe replay path: {value!r}")
    return path


def _is_build_content(path: PurePosixPath) -> bool:
    return any(part.lower() in _EXCLUDED_PARTS for part in path.parts)


def _sanitize_network_url(remote: str) -> str:
    parsed = urllib.parse.urlsplit(remote)
    if not parsed.scheme or not parsed.hostname:
        return remote
    username = parsed.username if parsed.scheme.lower() == "ssh" else None
    userinfo = f"{urllib.parse.quote(username, safe='')}@" if username else ""
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, f"{userinfo}{host}", parsed.path, "", ""))


def _sanitize_fetch_url(remote_url: str, repo_path: Path) -> tuple[str, str]:
    remote = remote_url.strip()
    normalized = normalize_remote_url(remote)
    if normalized:
        return _sanitize_network_url(remote), normalized

    local = Path(remote)
    if not local.is_absolute():
        local = repo_path / local
    try:
        resolved = local.resolve(strict=True)
    except OSError as err:
        raise ReplayError(f"repository remote is not usable for replay: {remote_url!r}") from err
    return str(resolved), resolved.as_uri().lower()


def _capture_untracked(
    repo_path: Path,
    *,
    max_file_bytes: int,
    reviewed_paths: set[str],
) -> tuple[ReplayUntrackedFile, ...]:
    output = _git_bytes(repo_path, ["ls-files", "--others", "--exclude-standard", "-z"])
    entries: list[ReplayUntrackedFile] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        try:
            relative = raw.decode("utf-8")
        except UnicodeDecodeError as err:
            raise ReplayError("untracked path is not valid UTF-8") from err
        safe = _safe_relative_path(relative)
        if _is_build_content(safe):
            continue
        if safe.as_posix() not in reviewed_paths:
            raise ReplayError(
                "untracked file is outside the reviewed change set; stage, commit, "
                f"ignore, or remove it before capture: {relative}"
            )
        candidate = repo_path.joinpath(*safe.parts)
        try:
            info = candidate.lstat()
        except OSError as err:
            raise ReplayError(f"cannot inspect untracked file {relative}: {err}") from err
        if stat.S_ISLNK(info.st_mode):
            raise ReplayError(f"untracked symlink cannot be captured safely: {relative}")
        if not stat.S_ISREG(info.st_mode):
            raise ReplayError(f"untracked path is not a regular file: {relative}")
        if info.st_size > max_file_bytes:
            raise ReplayError(
                f"untracked file exceeds {max_file_bytes} byte replay cap: {relative} "
                f"({info.st_size} bytes)"
            )
        content = candidate.read_bytes()
        if len(content) != info.st_size:
            raise ReplayError(f"untracked file changed while being captured: {relative}")
        entries.append(
            ReplayUntrackedFile(
                path=safe.as_posix(),
                size=len(content),
                sha256=_sha256(content),
                content_base64=base64.b64encode(content).decode("ascii"),
                executable=bool(info.st_mode & stat.S_IXUSR),
            )
        )
    return tuple(entries)


def _relative_session_path(path: str, session_dir: Path, label: str) -> str:
    try:
        relative = Path(path).resolve(strict=True).relative_to(session_dir.resolve(strict=True))
    except (OSError, ValueError) as err:
        raise ReplayError(f"{label} must be inside the captured session directory") from err
    return _safe_relative_path(relative.as_posix()).as_posix()


def _capture_hint_artifact(
    *,
    session_dir: Path,
    hint_path: str | None,
    hint_grant_dir: str | None,
) -> ReplayHintArtifact | None:
    if hint_path is None and hint_grant_dir is None:
        return None
    if not hint_path or not hint_grant_dir:
        raise ReplayError("replay hint path and grant directory must be captured together")
    return ReplayHintArtifact(
        recorded_path=hint_path,
        pointer_path=_relative_session_path(hint_path, session_dir, "hint path"),
        grant_dir=_relative_session_path(hint_grant_dir, session_dir, "hint grant directory"),
    )


def _sanitize_identity(identity: AdoIdentity, repo_path: Path) -> AdoIdentity:
    remote_url, _ = _sanitize_fetch_url(identity.remote_url, repo_path)
    return AdoIdentity(
        org=identity.org,
        project=identity.project,
        repo_name=identity.repo_name,
        remote_url=remote_url,
        host=identity.host,
        repository_id=identity.repository_id,
        project_id=identity.project_id,
    )


def _core_autocrlf(repo_path: Path) -> str:
    proc = subprocess.run(
        ["git", "config", "--get", "core.autocrlf"],
        cwd=repo_path,
        capture_output=True,
        timeout=30,
    )
    value = proc.stdout.decode("ascii", "replace").strip().lower()
    return value if value in {"true", "false", "input"} else "false"


def capture_replay_context(
    *,
    repo_name: str,
    repo_path: Path,
    remote_url: str | None,
    mode: str,
    source_sha: str,
    base_sha: str,
    source_branch: str,
    base_branch: str,
    ado_identities: list[AdoIdentity],
    changed_files: list[str],
    session_header: str,
    session_dir: Path,
    hint_path: str | None,
    hint_grant_dir: str | None,
    capture_overlay: bool,
    max_patch_bytes: int = MAX_TRACKED_PATCH_BYTES,
    max_file_bytes: int = MAX_UNTRACKED_FILE_BYTES,
    max_aggregate_bytes: int = MAX_OVERLAY_BYTES,
) -> ReplayContext:
    """Capture replay inputs before execution without changing review-diff semantics."""
    if not remote_url:
        raise ReplayError("repository has no origin remote; exact replay cannot be captured")
    if not _SHA.fullmatch(source_sha) or not _SHA.fullmatch(base_sha):
        raise ReplayError("replay source/base revisions must be full commit SHAs")
    fetch_url, normalized = _sanitize_fetch_url(remote_url, repo_path)
    actual_head = _git_bytes(repo_path, ["rev-parse", "HEAD"]).decode("ascii").strip()
    if actual_head.lower() != source_sha.lower():
        raise ReplayError(
            f"workspace HEAD changed during capture: expected {source_sha}, found {actual_head}"
        )

    reviewed_paths = {_safe_relative_path(path).as_posix() for path in changed_files}
    overlay: ReplayWorkspaceOverlay | None = None
    if capture_overlay:
        patch = _git_bytes(
            repo_path,
            ["diff", "--binary", "--full-index", "--no-ext-diff", "HEAD", "--"],
        )
        if len(patch) > max_patch_bytes:
            raise ReplayError(
                f"tracked workspace patch exceeds {max_patch_bytes} byte replay cap "
                f"({len(patch)} bytes)"
            )
        untracked = _capture_untracked(
            repo_path,
            max_file_bytes=max_file_bytes,
            reviewed_paths=reviewed_paths,
        )
        aggregate = len(patch) + sum(item.size for item in untracked)
        if aggregate > max_aggregate_bytes:
            raise ReplayError(
                f"dirty workspace overlay exceeds {max_aggregate_bytes} byte aggregate cap "
                f"({aggregate} bytes)"
            )
        if patch or untracked:
            overlay = ReplayWorkspaceOverlay(
                tracked_patch_base64=base64.b64encode(patch).decode("ascii"),
                tracked_patch_size=len(patch),
                tracked_patch_sha256=_sha256(patch),
                untracked_files=untracked,
                aggregate_size=aggregate,
                core_autocrlf=_core_autocrlf(repo_path),
            )

    return ReplayContext(
        version=REPLAY_CONTEXT_VERSION,
        repository=ReplayRepository(repo_name, fetch_url, normalized),
        revision=ReplayRevision(mode, source_sha, base_sha, source_branch, base_branch),
        ado_identities=tuple(_sanitize_identity(item, repo_path) for item in ado_identities),
        changed_files=tuple(sorted(reviewed_paths)),
        session_header=session_header,
        recorded_workspace_path=str(repo_path),
        hint_artifact=_capture_hint_artifact(
            session_dir=session_dir,
            hint_path=hint_path,
            hint_grant_dir=hint_grant_dir,
        ),
        workspace_overlay=overlay,
        provider_identities=tuple(
            _provider_identity(_sanitize_identity(item, repo_path)) for item in ado_identities
        ),
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReplayError(f"{label} must be a JSON object")
    return value


def _string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ReplayError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReplayError(f"{label} must be a non-negative integer")
    return value


def _decode_checked(content: Any, size: Any, digest: Any, label: str) -> bytes:
    encoded = _string(content, f"{label}.content", allow_empty=True)
    expected_size = _integer(size, f"{label}.size")
    expected_hash = _string(digest, f"{label}.sha256")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as err:
        raise ReplayError(f"{label}.content is not valid base64") from err
    if len(decoded) != expected_size:
        raise ReplayError(f"{label} size mismatch")
    if _sha256(decoded) != expected_hash:
        raise ReplayError(f"{label} sha256 mismatch")
    return decoded


def _parse_overlay(value: Any) -> ReplayWorkspaceOverlay | None:
    if value is None:
        return None
    raw = _object(value, "workspaceOverlay")
    patch = _object(raw.get("trackedPatch"), "workspaceOverlay.trackedPatch")
    if patch.get("encoding") != "base64":
        raise ReplayError("workspaceOverlay.trackedPatch.encoding must be 'base64'")
    patch_bytes = _decode_checked(
        patch.get("content"),
        patch.get("size"),
        patch.get("sha256"),
        "workspaceOverlay.trackedPatch",
    )
    if len(patch_bytes) > MAX_TRACKED_PATCH_BYTES:
        raise ReplayError("saved tracked workspace patch exceeds the replay cap")
    raw_files = raw.get("untrackedFiles")
    if not isinstance(raw_files, list):
        raise ReplayError("workspaceOverlay.untrackedFiles must be an array")
    files: list[ReplayUntrackedFile] = []
    for index, item in enumerate(raw_files):
        entry = _object(item, f"workspaceOverlay.untrackedFiles[{index}]")
        if entry.get("encoding") != "base64":
            raise ReplayError(f"workspaceOverlay.untrackedFiles[{index}].encoding must be 'base64'")
        path = _safe_relative_path(
            _string(entry.get("path"), f"workspaceOverlay.untrackedFiles[{index}].path")
        )
        if _is_build_content(path):
            raise ReplayError(f"saved replay contains excluded content: {path.as_posix()}")
        content = _decode_checked(
            entry.get("content"),
            entry.get("size"),
            entry.get("sha256"),
            f"workspaceOverlay.untrackedFiles[{index}]",
        )
        if len(content) > MAX_UNTRACKED_FILE_BYTES:
            raise ReplayError(f"saved untracked file exceeds the replay cap: {path.as_posix()}")
        executable = entry.get("executable")
        if not isinstance(executable, bool):
            raise ReplayError(
                f"workspaceOverlay.untrackedFiles[{index}].executable must be boolean"
            )
        files.append(
            ReplayUntrackedFile(
                path=path.as_posix(),
                size=len(content),
                sha256=_sha256(content),
                content_base64=base64.b64encode(content).decode("ascii"),
                executable=executable,
            )
        )
    aggregate = _integer(raw.get("aggregateSize"), "workspaceOverlay.aggregateSize")
    calculated = len(patch_bytes) + sum(item.size for item in files)
    if aggregate != calculated:
        raise ReplayError("workspaceOverlay aggregate size mismatch")
    if aggregate > MAX_OVERLAY_BYTES:
        raise ReplayError("saved dirty workspace overlay exceeds the aggregate replay cap")
    core_autocrlf = _string(raw.get("coreAutocrlf"), "workspaceOverlay.coreAutocrlf")
    if core_autocrlf not in {"true", "false", "input"}:
        raise ReplayError("workspaceOverlay.coreAutocrlf must be true, false, or input")
    return ReplayWorkspaceOverlay(
        tracked_patch_base64=base64.b64encode(patch_bytes).decode("ascii"),
        tracked_patch_size=len(patch_bytes),
        tracked_patch_sha256=_sha256(patch_bytes),
        untracked_files=tuple(files),
        aggregate_size=aggregate,
        core_autocrlf=core_autocrlf,
    )


def replay_context_from_dict(payload: dict[str, Any]) -> ReplayContext:
    version = payload.get("version")
    if version == REPLAY_CONTEXT_V1:
        return _replay_v1_from_dict(payload)
    if version != REPLAY_CONTEXT_VERSION:
        raise ReplayError(
            f"unsupported replay context version {version!r}; "
            f"expected {REPLAY_CONTEXT_V1} or {REPLAY_CONTEXT_VERSION}"
        )
    raw_identities = payload.get("providerIdentities")
    if not isinstance(raw_identities, list):
        raise ReplayError("providerIdentities must be an array")
    try:
        identities = tuple(RepositoryIdentity.from_dict(item) for item in raw_identities)
    except ValueError as err:
        raise ReplayError(f"invalid provider identity: {err}") from err
    for index, identity in enumerate(identities):
        remote_url = identity.extensions.get("remoteUrl")
        if isinstance(remote_url, str) and _sanitize_network_url(remote_url) != remote_url:
            raise ReplayError(
                f"providerIdentities[{index}].extensions.remoteUrl must not contain credentials"
            )
    legacy_payload = dict(payload)
    legacy_payload["version"] = REPLAY_CONTEXT_V1
    legacy_payload["adoIdentities"] = [
        _ado_identity_to_dict(ado)
        for identity in identities
        if (ado := _ado_identity(identity)) is not None
    ]
    legacy_payload.pop("providerIdentities", None)
    context = _replay_v1_from_dict(legacy_payload)
    return ReplayContext(
        version=REPLAY_CONTEXT_VERSION,
        repository=context.repository,
        revision=context.revision,
        ado_identities=context.ado_identities,
        changed_files=context.changed_files,
        session_header=context.session_header,
        recorded_workspace_path=context.recorded_workspace_path,
        hint_artifact=context.hint_artifact,
        workspace_overlay=context.workspace_overlay,
        provider_identities=identities,
    )


def _replay_v1_from_dict(payload: dict[str, Any]) -> ReplayContext:
    version = payload.get("version")
    if version != REPLAY_CONTEXT_V1:
        raise ReplayError(f"unsupported replay v1 context version {version!r}")
    repository = _object(payload.get("repository"), "repository")
    revision = _object(payload.get("revision"), "revision")
    source_sha = _string(revision.get("sourceSha"), "revision.sourceSha")
    base_sha = _string(revision.get("baseSha"), "revision.baseSha")
    if not _SHA.fullmatch(source_sha) or not _SHA.fullmatch(base_sha):
        raise ReplayError("revision source/base SHAs must be full hexadecimal commit IDs")
    raw_identities = payload.get("adoIdentities")
    if not isinstance(raw_identities, list):
        raise ReplayError("adoIdentities must be an array")
    identities: list[AdoIdentity] = []
    for index, value in enumerate(raw_identities):
        item = _object(value, f"adoIdentities[{index}]")
        remote_url = _string(
            item.get("remoteUrl"),
            f"adoIdentities[{index}].remoteUrl",
            allow_empty=True,
        )
        if remote_url and _sanitize_network_url(remote_url) != remote_url:
            raise ReplayError(
                f"adoIdentities[{index}].remoteUrl must not contain credentials or query data"
            )
        identities.append(
            AdoIdentity(
                org=_string(item.get("org"), f"adoIdentities[{index}].org"),
                project=_string(item.get("project"), f"adoIdentities[{index}].project"),
                repo_name=_string(item.get("repoName"), f"adoIdentities[{index}].repoName"),
                remote_url=remote_url,
                host=_string(item.get("host"), f"adoIdentities[{index}].host"),
                repository_id=(
                    _string(item["repositoryId"], f"adoIdentities[{index}].repositoryId")
                    if item.get("repositoryId") is not None
                    else None
                ),
                project_id=(
                    _string(item["projectId"], f"adoIdentities[{index}].projectId")
                    if item.get("projectId") is not None
                    else None
                ),
            )
        )
    changed = payload.get("changedFiles")
    if not isinstance(changed, list) or not all(isinstance(path, str) for path in changed):
        raise ReplayError("changedFiles must be an array of strings")
    changed_paths = tuple(_safe_relative_path(path).as_posix() for path in changed)
    fetch_url = _string(repository.get("fetchUrl"), "repository.fetchUrl")
    if _sanitize_network_url(fetch_url) != fetch_url:
        raise ReplayError("repository.fetchUrl must not contain credentials or query data")
    recorded_remote = _string(
        repository.get("normalizedRemoteUrl"), "repository.normalizedRemoteUrl"
    )
    normalized_fetch = normalize_remote_url(fetch_url)
    if normalized_fetch is None:
        normalized_fetch = Path(fetch_url).expanduser().resolve(strict=False).as_uri().lower()
    if normalized_fetch != canonical_ado_identity(recorded_remote):
        raise ReplayError("repository fetch URL does not match its normalized remote identity")
    hint_raw = payload.get("hintArtifact")
    hint_artifact = None
    if hint_raw is not None:
        hint = _object(hint_raw, "hintArtifact")
        hint_artifact = ReplayHintArtifact(
            recorded_path=_string(hint.get("recordedPath"), "hintArtifact.recordedPath"),
            pointer_path=_safe_relative_path(
                _string(hint.get("pointerPath"), "hintArtifact.pointerPath")
            ).as_posix(),
            grant_dir=_safe_relative_path(
                _string(hint.get("grantDir"), "hintArtifact.grantDir")
            ).as_posix(),
        )
    return ReplayContext(
        version=version,
        repository=ReplayRepository(
            name=_string(repository.get("name"), "repository.name"),
            fetch_url=fetch_url,
            normalized_remote_url=normalized_fetch,
        ),
        revision=ReplayRevision(
            mode=_string(revision.get("mode"), "revision.mode"),
            source_sha=source_sha,
            base_sha=base_sha,
            source_branch=_string(revision.get("sourceBranch"), "revision.sourceBranch"),
            base_branch=_string(revision.get("baseBranch"), "revision.baseBranch"),
        ),
        ado_identities=tuple(identities),
        changed_files=changed_paths,
        session_header=_string(payload.get("sessionHeader"), "sessionHeader", allow_empty=True),
        recorded_workspace_path=_string(payload.get("workspacePath"), "workspacePath"),
        hint_artifact=hint_artifact,
        workspace_overlay=_parse_overlay(payload.get("workspaceOverlay")),
        provider_identities=tuple(_provider_identity(identity) for identity in identities),
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ReplayError(f"cannot read {path}: {err}") from err
    return _object(value, str(path))


def load_replay_session(path: str | Path) -> ReplaySession:
    source = Path(path).expanduser().resolve()
    if not source.is_dir():
        raise ReplayError(
            "--input-from requires a complete session directory, not an artifact file"
        )
    missing = [name for name in REQUIRED_SESSION_FILES if not (source / name).is_file()]
    if missing:
        raise ReplayError(
            f"incomplete replay session {source}: missing {', '.join(sorted(missing))}"
        )
    context = replay_context_from_dict(_read_json(source / REPLAY_CONTEXT_FILE))
    payloads = _read_json(source / "source-payloads.json")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in payloads.items()):
        raise ReplayError("source-payloads.json must map source-node names to strings")
    configuration = _read_json(source / "configuration.json")
    graph_sha = configuration.get("graphConfigSha")
    return ReplaySession(
        session_dir=source,
        context=context,
        source_payloads=dict(payloads),
        graph_config_sha=graph_sha if isinstance(graph_sha, str) else None,
    )


def _verify_commit(repo: Path, sha: str, label: str) -> None:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        timeout=30,
    )
    if not result.returncode:
        return
    subprocess.run(
        ["git", "fetch", "origin", sha],
        cwd=repo,
        capture_output=True,
        timeout=300,
    )
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise ReplayError(f"recorded {label} commit is unavailable: {sha}")


def _safe_destination(root: Path, relative: str) -> Path:
    safe = _safe_relative_path(relative)
    current = root
    for part in safe.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ReplayError(f"replay path crosses a symlink: {relative}")
        if current.exists() and not current.is_dir():
            raise ReplayError(f"replay path parent is not a directory: {relative}")
        current.mkdir(exist_ok=True)
    destination = root.joinpath(*safe.parts)
    if destination.is_symlink() or destination.exists():
        raise ReplayError(f"replay untracked path would overwrite repository content: {relative}")
    try:
        destination.resolve().relative_to(root.resolve())
    except ValueError as err:
        raise ReplayError(f"replay path escapes the workspace: {relative}") from err
    return destination


def _session_artifact(session_dir: Path, relative: str, label: str) -> Path:
    root = session_dir.resolve(strict=True)
    candidate = root.joinpath(*_safe_relative_path(relative).parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as err:
        raise ReplayError(f"{label} is missing or escapes the replay session") from err
    current = root
    for part in _safe_relative_path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ReplayError(f"{label} crosses a symlink")
    return resolved


def _replace_recorded_path(header: str, recorded: str, restored: str, label: str) -> str:
    encoded_recorded = json.dumps(recorded, ensure_ascii=False)
    encoded_restored = json.dumps(restored, ensure_ascii=False)
    if encoded_recorded in header:
        return header.replace(encoded_recorded, encoded_restored)
    if recorded in header:
        return header.replace(recorded, restored)
    raise ReplayError(f"saved session header does not contain its recorded {label}")


def materialize_replay_access(
    session: ReplaySession,
    workspace_path: Path,
) -> tuple[str, tuple[str, ...]]:
    """Remap path-bearing prompt context to the restored workspace/session."""
    context = session.context
    header = _replace_recorded_path(
        context.session_header,
        context.recorded_workspace_path,
        str(workspace_path),
        "workspace path",
    )
    hint = context.hint_artifact
    if hint is None:
        return header, ()
    pointer = _session_artifact(session.session_dir, hint.pointer_path, "saved hint path")
    grant = _session_artifact(session.session_dir, hint.grant_dir, "saved hint grant directory")
    if not grant.is_dir():
        raise ReplayError("saved hint grant path is not a directory")
    header = _replace_recorded_path(header, hint.recorded_path, str(pointer), "hint path")
    return header, (str(grant),)


def apply_workspace_overlay(workspace: Path, overlay: ReplayWorkspaceOverlay | None) -> None:
    if overlay is None:
        return
    patch = _decode_checked(
        overlay.tracked_patch_base64,
        overlay.tracked_patch_size,
        overlay.tracked_patch_sha256,
        "workspaceOverlay.trackedPatch",
    )
    if patch:
        _git_bytes(
            workspace,
            [
                "-c",
                f"core.autocrlf={overlay.core_autocrlf}",
                "reset",
                "--hard",
                "HEAD",
            ],
        )
        _git_bytes(
            workspace,
            [
                "-c",
                f"core.autocrlf={overlay.core_autocrlf}",
                "apply",
                "--check",
                "--binary",
                "--whitespace=nowarn",
                "-",
            ],
            input_data=patch,
        )
        _git_bytes(
            workspace,
            [
                "-c",
                f"core.autocrlf={overlay.core_autocrlf}",
                "apply",
                "--binary",
                "--whitespace=nowarn",
                "-",
            ],
            input_data=patch,
        )
    for item in overlay.untracked_files:
        content = _decode_checked(item.content_base64, item.size, item.sha256, item.path)
        destination = _safe_destination(workspace, item.path)
        try:
            with destination.open("xb") as stream:
                stream.write(content)
            if item.executable:
                destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
        except OSError as err:
            raise ReplayError(f"cannot restore untracked file {item.path}: {err}") from err


def restore_replay_session(
    input_from: str | Path,
    *,
    settings: WorkspaceSettings,
    worktree_dir: Path,
) -> RestoredReplay:
    session = load_replay_session(input_from)
    context = session.context
    workspace = None
    try:
        workspace = build_review_workspace(
            WorkspaceRequest(
                mode="pr",
                remote_url=context.repository.fetch_url,
                source_sha=context.revision.source_sha,
                base_sha=context.revision.base_sha,
                target_ref=context.revision.base_branch,
            ),
            settings=settings,
            worktree_dir=worktree_dir,
        )
        _verify_commit(workspace.path, context.revision.source_sha, "source")
        _verify_commit(workspace.path, context.revision.base_sha, "base")
        apply_workspace_overlay(workspace.path, context.workspace_overlay)
        return RestoredReplay(session=session, workspace=workspace)
    except Exception:
        if workspace is not None:
            workspace.cleanup()
        raise

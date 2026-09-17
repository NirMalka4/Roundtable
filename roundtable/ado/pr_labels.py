"""Maintain the one current Roundtable adoption label on an ADO PR."""

from __future__ import annotations

import json
from collections.abc import Callable

from roundtable.ado_client import ado_auth_header, build_ado_base_url, encode_segment
from roundtable.adoption import parse_label
from roundtable.inputs import PrReference

from .pr_threads import Transport, TransportError, _UrllibTransport

# The PR labels resource is a preview-only ADO surface (the GA threads endpoint
# uses plain ``7.1``); a version bump here would silently 404 the labels calls.
_LABELS_API_VERSION = "7.1-preview.1"


class PrLabelClient:
    """Maintain the one current Roundtable adoption label on a PR."""

    def __init__(
        self,
        pr: PrReference,
        *,
        host: str | None = None,
        timeout: float = 30.0,
        transport: Transport | None = None,
    ) -> None:
        self._pr = pr
        self._timeout = timeout
        self._transport = transport or _UrllibTransport()
        resolved_host = host or pr.host or "dev.azure.com"
        base = build_ado_base_url(resolved_host, pr.org)
        self._labels_endpoint = (
            f"{base}/{encode_segment(pr.project)}/_apis/git/repositories/"
            f"{encode_segment(pr.repo_name)}/pullRequests/{pr.pr_id}/labels"
        )
        auth = ado_auth_header()
        if not auth:
            raise RuntimeError(
                "Labelling requires ADO credentials. Set ROUNDTABLE_ADO_PAT (or "
                "AZURE_DEVOPS_PAT) with scope Code (write), or sign in with 'az login'."
            )
        self._headers = {"Authorization": auth, "Content-Type": "application/json"}

    def _url(self, suffix: str = "") -> str:
        return f"{self._labels_endpoint}{suffix}?api-version={_LABELS_API_VERSION}"

    def list_labels(self) -> list[dict]:
        """GET the PR's labels as ``[{id, name}, …]``."""
        status, body = self._transport.request(
            "GET", self._url(), headers=self._headers, body=None, timeout=self._timeout
        )
        if status != 200:
            raise RuntimeError(f"ADO labels GET failed (HTTP {status}).")
        return json.loads(body).get("value") or []

    def _add(self, name: str) -> None:
        body = json.dumps({"name": name}).encode("utf-8")
        status, _ = self._transport.request(
            "POST", self._url(), headers=self._headers, body=body, timeout=self._timeout
        )
        if status not in (200, 201):
            raise RuntimeError(f"ADO label POST failed (HTTP {status}).")

    def _remove(self, label: dict) -> None:
        # Prefer the stable label id; fall back to the (encoded) name.
        ident = label.get("id") or label.get("name") or ""
        status, _ = self._transport.request(
            "DELETE",
            self._url(f"/{encode_segment(str(ident))}"),
            headers=self._headers,
            body=None,
            timeout=self._timeout,
        )
        # 404 ⇒ already gone (idempotent delete); anything else is a real failure.
        if status not in (200, 204, 404):
            raise RuntimeError(f"ADO label DELETE failed (HTTP {status}).")

    def _roundtable_labels(self, existing: list[dict]) -> list[dict]:
        return [label for label in existing if parse_label(str(label.get("name", ""))) is not None]

    def apply_adoption_label(self, label: str) -> str:
        """Ensure ``label`` is the PR's only recognized Roundtable label.

        Returns ``present`` (already the only Roundtable label), ``added`` (was
        absent, none to replace), or ``replaced`` (a stale Roundtable label was
        removed)."""
        ours = self._roundtable_labels(self.list_labels())
        have_target = any(label_.get("name") == label for label_ in ours)
        stale = [label_ for label_ in ours if label_.get("name") != label]
        for label_ in stale:
            self._remove(label_)
        if not have_target:
            self._add(label)
        return "replaced" if stale else ("present" if have_target else "added")


def _default_client(pr: PrReference) -> PrLabelClient:
    return PrLabelClient(pr)


def stamp_adoption_label(
    pr: PrReference,
    label: str,
    *,
    client_factory: Callable[[PrReference], PrLabelClient] | None = None,
) -> tuple[str, str]:
    """Best-effort apply of an already encoded adoption label."""
    parsed = parse_label(label)
    if parsed is None:
        raise ValueError(f"invalid current-state Roundtable label: {label!r}")
    try:
        action = (client_factory or _default_client)(pr).apply_adoption_label(label)
    except (RuntimeError, TransportError, OSError, ValueError):
        return label, "failed"
    return label, action

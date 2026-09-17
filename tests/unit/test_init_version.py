"""Provenance-honest version resolution (``roundtable._resolve_version``).

A feed-installed wheel must report its clean ``X.Y.Z`` so ``version_label`` fires,
while an editable/source tree must report ``+dev`` so unreleased code never stamps
a clean release label. Both seams — the installed-metadata lookup and the editable
probe — are monkeypatched so the branch logic is tested offline.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import roundtable


def _resolve(monkeypatch, *, editable: bool, metadata: str | None):
    monkeypatch.setattr(roundtable, "current_install_is_editable", lambda: editable)
    if metadata is None:

        def _raise(_name):
            raise PackageNotFoundError

        monkeypatch.setattr(roundtable, "_pkg_version", _raise)
    else:
        monkeypatch.setattr(roundtable, "_pkg_version", lambda _name: metadata)
    return roundtable._resolve_version()


def test_feed_wheel_reports_clean_version(monkeypatch):
    assert _resolve(monkeypatch, editable=False, metadata="1.0.0") == "1.0.0"


def test_editable_install_gets_dev_suffix(monkeypatch):
    assert _resolve(monkeypatch, editable=True, metadata="1.0.0") == "1.0.0+dev"


def test_editable_with_existing_local_suffix_is_not_double_suffixed(monkeypatch):
    assert _resolve(monkeypatch, editable=True, metadata="1.0.0+abc") == "1.0.0+abc"


def test_source_checkout_without_dist_falls_back_to_authored_dev(monkeypatch):
    resolved = _resolve(monkeypatch, editable=True, metadata=None)
    assert resolved == f"{roundtable._authored_version}+dev"

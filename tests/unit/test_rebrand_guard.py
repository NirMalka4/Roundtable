"""The rebrand guard: the shipped engine carries no stray old-engine identity.

Proves (a) the live tree is clean, (b) an un-marked ``inspectorx_py`` / stray
``INSPECTORX_`` read is caught, and (c) a ``# rebrand-compat`` marker suppresses
retained compatibility references. The bundle name ``inspectorx`` and the
retained ``InspectorX-CLI`` tokens are deliberately NOT flagged.
"""

from __future__ import annotations

from roundtable.rebrand_guard import check_rebrand_guard


def test_live_tree_is_clean():
    assert check_rebrand_guard() == []


def _tree(tmp_path, rel: str, body: str):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    (tmp_path / "roundtable").mkdir(exist_ok=True)


def test_flags_unmarked_engine_token(tmp_path):
    _tree(tmp_path, "roundtable/leak.py", "import inspectorx_py.cli\n")
    out = check_rebrand_guard(tmp_path)
    assert len(out) == 1 and "roundtable/leak.py:1" in out[0]


def test_flags_stray_legacy_env_read(tmp_path):
    _tree(tmp_path, "roundtable/leak.py", 'x = os.environ["INSPECTORX_CONFIG"]\n')
    assert len(check_rebrand_guard(tmp_path)) == 1


def test_rebrand_compat_marker_suppresses(tmp_path):
    _tree(tmp_path, "roundtable/shim.py", "read = 'INSPECTORX_CONFIG'  # rebrand-compat\n")
    assert check_rebrand_guard(tmp_path) == []


def test_removed_console_compat_module_is_not_allowlisted(tmp_path):
    _tree(tmp_path, "roundtable/_compat.py", "legacy = 'inspectorx-py'\n")
    assert len(check_rebrand_guard(tmp_path)) == 1


def test_bundle_and_retained_tokens_not_flagged(tmp_path):
    _tree(
        tmp_path,
        "roundtable/keep.py",
        # bundle name/path + retained CLI identity are all legitimate
        'NAME = "inspectorx"\nPATH = "configs/inspectorx/schemas"\nTAG = "InspectorX-CLI"\n',
    )
    assert check_rebrand_guard(tmp_path) == []


def test_bundle_dir_is_not_scanned(tmp_path):
    _tree(tmp_path, "roundtable/configs/inspectorx/x.py", "legacy = 'inspectorx_py'\n")
    assert check_rebrand_guard(tmp_path) == []

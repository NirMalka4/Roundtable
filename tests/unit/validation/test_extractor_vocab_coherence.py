"""Layer 7 — extractor↔vocabulary coherence for the doctor.

Two guarantees, mirroring the Layer 5 tests:
  * the REAL schemas are fully coherent (zero errors), so a drift turns red; and
  * each rule fires on a synthetic drift built in a tmp schema dir (proving the
    check is not a silent no-op): a canonical name that doesn't ``$ref`` its def,
    an inline enum duplicating a vocab def, a bad adapter severity-map target, an
    adapter role key outside the vocab, and a $def missing its ``description``.

No agent/field/enum names are hardcoded structurally — drift is derived from the
real vocabulary, so new terms are covered without editing this file.
"""

from __future__ import annotations

import shutil

import yaml

from roundtable.bundle import resolve_bundle
from roundtable.graph.model import get_configuration
from roundtable.validation.coherence import validate_extractor_vocab_coherence
from roundtable.validation.gates import load_schema_document

_VOCAB_REL = "_shared/vocabulary.schema.yaml"
_SCHEMA_DIR = resolve_bundle("inspectorx") / "schemas"
_CONFIG = get_configuration(resolve_bundle("inspectorx"))


def _seed_schema_dir(tmp_path):
    """A tmp schema dir carrying the REAL ``_shared/`` vocabulary (so ``$defs`` are
    real) but NO agent schemas — the caller writes the drifted one it wants to test."""
    (tmp_path / "_shared").mkdir()
    shutil.copytree(_SCHEMA_DIR / "_shared", tmp_path / "_shared", dirs_exist_ok=True)
    return tmp_path


def _write(tmp_path, name, doc):
    (tmp_path / name).write_text(yaml.safe_dump(doc), encoding="utf-8")


def _vocab_defs():
    return load_schema_document(_VOCAB_REL, _SCHEMA_DIR)["$defs"]


# ── the real schemas are coherent ────────────────────────────────────────────


def test_real_schemas_have_no_vocab_coherence_errors():
    report = validate_extractor_vocab_coherence(_CONFIG.entries, schema_dir=_SCHEMA_DIR)
    assert report.errors == []


# ── each rule fires on a synthetic drift ─────────────────────────────────────


def test_canonical_field_name_must_ref_its_def(tmp_path):
    d = _seed_schema_dir(tmp_path)
    # 'severity' is a canonical $def; declaring it inline (no $ref) is drift.
    _write(d, "drift.schema.yaml", {"properties": {"severity": {"type": "string"}}})
    report = validate_extractor_vocab_coherence(_CONFIG.entries, schema_dir=d)
    assert any("shadows canonical vocabulary term 'severity'" in e for e in report.errors)


def test_inline_enum_duplicating_a_vocab_def_flagged(tmp_path):
    d = _seed_schema_dir(tmp_path)
    severity_values = _vocab_defs()["severity"]["enum"]
    # A differently-named field that copies the severity enum verbatim.
    _write(d, "drift.schema.yaml", {"properties": {"level": {"enum": list(severity_values)}}})
    report = validate_extractor_vocab_coherence(_CONFIG.entries, schema_dir=d)
    assert any(
        "inlines an enum identical to vocabulary $def 'severity'" in e for e in report.errors
    )


def test_adapter_severity_map_target_must_be_a_real_severity(tmp_path):
    d = _seed_schema_dir(tmp_path)
    _write(
        d,
        "drift.schema.yaml",
        {
            "properties": {
                "rows": {
                    "type": "array",
                    "x-finding-array": True,
                    "x-finding-adapter": {
                        "severity": {"from": "outcome", "map": {"FAILURE": "sev5"}}
                    },
                }
            }
        },
    )
    report = validate_extractor_vocab_coherence(_CONFIG.entries, schema_dir=d)
    assert any("'sev5' is not a `severity` enum value" in e for e in report.errors)


def test_adapter_role_key_must_be_a_vocab_term(tmp_path):
    d = _seed_schema_dir(tmp_path)
    _write(
        d,
        "drift.schema.yaml",
        {
            "properties": {
                "rows": {
                    "type": "array",
                    "x-finding-array": True,
                    "x-finding-adapter": {"id": "not_a_vocab_term"},
                }
            }
        },
    )
    report = validate_extractor_vocab_coherence(_CONFIG.entries, schema_dir=d)
    assert any(
        "'not_a_vocab_term', which is not a canonical vocabulary term" in e for e in report.errors
    )


def test_vocab_def_without_description_flagged(tmp_path):
    (tmp_path / "_shared").mkdir()
    vocab = {"$defs": {"lonely": {"type": "string"}}}
    (tmp_path / "_shared" / "vocabulary.schema.yaml").write_text(
        yaml.safe_dump(vocab), encoding="utf-8"
    )
    report = validate_extractor_vocab_coherence(_CONFIG.entries, schema_dir=tmp_path)
    assert any("$def 'lonely' has no non-empty `description`" in e for e in report.errors)

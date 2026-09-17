"""``doctor`` reports the same resolved config a review would run with.

The defect this pins: ``doctor`` used to read the *raw* settings dataclass, so a key
nobody set printed ``(unset)  [default]`` — naming the winning layer while withholding
that layer's value — while a review printed the resolved ``3`` / ``6`` / ``<path>`` for
the same key. Two surfaces, one config, contradictory answers.

Also pinned: a clean run says only what doctor *found*. The import boundaries and the
rebrand guard still run and still fail loudly; they just stop narrating success.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from roundtable import cli
from roundtable.settings.effective import build_review_config, settings_params
from roundtable.settings.workspace import get_settings

_SHARED_KEYS = ("max_attempts", "concurrency", "artifacts_dir", "ado.auth")


def _review_args() -> Namespace:
    return Namespace(
        max_attempts=None,
        concurrency=None,
        artifacts_dir=None,
        base_branch=None,
        pr=None,
        session_reuse=True,
        dump_prompts=False,
        dry_run=True,
        simulate=False,
    )


@pytest.mark.parametrize("key", _SHARED_KEYS)
def test_doctor_and_review_agree_on_every_shared_key(key):
    settings = get_settings()
    doctor = {p.name: p for p in settings_params(settings)}
    review = {p.name: p for p in build_review_config(settings, _review_args()).params}
    assert (doctor[key].value, doctor[key].source) == (review[key].value, review[key].source)


def test_a_boundary_violation_still_fails(monkeypatch):
    monkeypatch.setattr(cli, "check_rebrand_guard", lambda: ["some/file.py: stray identity"])
    assert cli._cmd_doctor(Namespace(static=True)) != 0

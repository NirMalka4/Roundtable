"""Regression guard: the ``--pr`` help must not misstate the auth requirement.

PR-metadata auth resolves PAT-first then falls back to an AAD token minted from
the operator's ``az login`` (``inputs/pr_diff.py`` ``_ado_auth_header``). Earlier
help text claimed a PAT was *required* and omitted the ``az login`` path, which
misdirects users who are already signed in with the Azure CLI. This test pins
the corrected wording for both PR-mode commands.
"""

from __future__ import annotations

import argparse

import pytest

from roundtable import cli


def _pr_help(command: str) -> str:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices[command].format_help()


@pytest.mark.parametrize("command", ["review", "inputs"])
def test_pr_help_mentions_az_login_and_not_pat_only(command: str) -> None:
    # argparse hard-wraps help text, so normalize whitespace before matching.
    help_text = " ".join(_pr_help(command).split())
    assert "az login" in help_text
    # The old, misleading phrasing must not return.
    assert "needs ROUNDTABLE_ADO_PAT" not in help_text

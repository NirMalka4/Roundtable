"""config.effective — the single place that stamps the CLI-flag layer on top of
the ``env > roundtable.yaml > default`` resolution and records which layer won.

Contract under test:

* ``build_review_config`` resolves ``flag > env > file > default`` for the two
  flag-backed params (``max_attempts``, ``artifacts_dir``), and records the winning
  layer as ``source``.
* Pure CLI toggles are carried through with source ``cli``; ``ado.auth`` carries
  the settings layer (it affects PR-mode auth).
* ``as_trace`` is JSON-safe; ``render_params`` produces an aligned table.
* ``settings_params`` is the shared spine: the same key resolves to the same value
  and layer whether a review or ``doctor`` renders it.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from roundtable.settings.effective import (
    UNSET_FALLBACK,
    build_review_config,
    render_params,
    settings_params,
)
from roundtable.settings.workspace import default_artifacts_root, load_settings


def _args(**over):
    base = {
        "max_attempts": None,
        "concurrency": None,
        "artifacts_dir": None,
        "base_branch": None,
        "pr": None,
        "session_reuse": True,
        "dump_prompts": True,
        "dry_run": False,
        "simulate": False,
    }
    base.update(over)
    return Namespace(**base)


def _cfg(settings, args):
    return build_review_config(settings, args)


def _by_name(cfg):
    return {p.name: p for p in cfg.params}


def test_all_defaults_when_no_flag_env_or_file(tmp_path):
    settings = load_settings(start_dir=tmp_path, env={})
    cfg = _cfg(settings, _args())
    p = _by_name(cfg)
    assert cfg.max_attempts == 3
    assert p["max_attempts"].source == "default"
    assert cfg.artifacts_from_default is True
    assert cfg.artifacts_root == default_artifacts_root()
    assert p["artifacts_dir"].source == "default"


def test_flag_beats_env_beats_file_beats_default(tmp_path):
    (tmp_path / "roundtable.yaml").write_text(
        "max_attempts: 5\nartifacts_dir: /from/file\n", encoding="utf-8"
    )
    settings = load_settings(start_dir=tmp_path, env={"ROUNDTABLE_MAX_ATTEMPTS": "8"})
    # No flag: env wins for max_attempts, file wins for artifacts_dir.
    cfg = _cfg(settings, _args())
    p = _by_name(cfg)
    assert cfg.max_attempts == 8
    assert p["max_attempts"].source == "env:ROUNDTABLE_MAX_ATTEMPTS"
    assert cfg.artifacts_root == Path("/from/file")
    assert p["artifacts_dir"].source == "roundtable.yaml"

    # Flag passed: it wins outright over env/file.
    cfg2 = _cfg(settings, _args(max_attempts=9, artifacts_dir="/from/flag"))
    p2 = _by_name(cfg2)
    assert cfg2.max_attempts == 9
    assert p2["max_attempts"].source == "flag:--max-attempts"
    assert cfg2.artifacts_root == Path("/from/flag")
    assert p2["artifacts_dir"].source == "flag:--artifacts-dir"


def test_artifacts_dir_flag_marks_not_default(tmp_path):
    settings = load_settings(start_dir=tmp_path, env={})
    cfg = _cfg(settings, _args(artifacts_dir="/tmp/here"))
    assert cfg.artifacts_from_default is False
    assert cfg.artifacts_root == Path("/tmp/here")
    assert _by_name(cfg)["artifacts_dir"].source == "flag:--artifacts-dir"


def test_toggles_and_ado_auth_are_captured(tmp_path):
    settings = load_settings(start_dir=tmp_path, env={"ROUNDTABLE_ADO_AUTH": "az-login"})
    cfg = _cfg(settings, _args(session_reuse=False, pr="123"))
    p = _by_name(cfg)
    assert p["session_reuse"].value is False and p["session_reuse"].source == "cli"
    assert p["pr"].value == "123" and p["pr"].source == "cli"
    assert p["ado.auth"].value == "az-login"
    assert p["ado.auth"].source == "env:ROUNDTABLE_ADO_AUTH"


def test_as_trace_is_json_safe(tmp_path):
    settings = load_settings(start_dir=tmp_path, env={})
    cfg = _cfg(settings, _args(artifacts_dir="/tmp/x"))
    trace = cfg.as_trace()
    assert {"name", "value", "source"} <= set(trace[0])
    # Path values are stringified; bools/None/str stay as-is.
    for row in trace:
        assert isinstance(row["value"], (str, bool, int, float)) or row["value"] is None


def test_render_params_aligns_and_labels(tmp_path):
    settings = load_settings(start_dir=tmp_path, env={})
    cfg = _cfg(settings, _args())
    out = render_params(cfg.params, title="cfg")
    assert out.startswith("cfg:")
    assert "max_attempts" in out and "= 3  [default]" in out
    assert "session_reuse" in out and "[cli]" in out


def test_settings_params_reflects_layers(tmp_path):
    (tmp_path / "roundtable.yaml").write_text("max_attempts: 7\n", encoding="utf-8")
    settings = load_settings(start_dir=tmp_path, env={})
    table = {p.name: p for p in settings_params(settings)}
    assert table["max_attempts"].value == 7
    assert table["max_attempts"].source == "roundtable.yaml"


def test_shared_keys_render_identically_with_and_without_review_args(tmp_path):
    """The spine is one function, so ``doctor`` and a review cannot disagree about a
    key neither of them overrode — the defect this replaced."""
    settings = load_settings(start_dir=tmp_path, env={})
    doctor = {p.name: (p.value, p.source) for p in settings_params(settings)}
    review = {p.name: (p.value, p.source) for p in _cfg(settings, _args()).params}
    assert doctor
    assert all(review[name] == value for name, value in doctor.items())


def test_settings_params_resolves_builtin_defaults_not_unset(tmp_path):
    settings = load_settings(start_dir=tmp_path, env={})
    table = {p.name: p for p in settings_params(settings)}
    assert (table["max_attempts"].value, table["max_attempts"].source) == (3, "default")
    assert (table["concurrency"].value, table["concurrency"].source) == (6, "default")
    assert table["artifacts_dir"].value is not None


def test_settings_params_states_the_fallback_for_keys_with_no_default(tmp_path):
    settings = load_settings(start_dir=tmp_path, env={})
    table = {p.name: p for p in settings_params(settings)}
    assert table["prompt_dir"].value is None
    assert table["prompt_dir"].fallback
    assert table["mcp_npm_registry"].fallback


def test_concurrency_resolves_flag_over_env_over_file_over_default(tmp_path):
    (tmp_path / "roundtable.yaml").write_text("concurrency: 5\n", encoding="utf-8")

    settings = load_settings(start_dir=tmp_path, env={})
    cfg = _cfg(settings, _args())
    assert cfg.concurrency == 5
    assert _by_name(cfg)["concurrency"].source == "roundtable.yaml"

    settings = load_settings(start_dir=tmp_path, env={"ROUNDTABLE_CONCURRENCY": "9"})
    cfg = _cfg(settings, _args())
    assert cfg.concurrency == 9
    assert _by_name(cfg)["concurrency"].source == "env:ROUNDTABLE_CONCURRENCY"

    cfg = _cfg(settings, _args(concurrency=2))
    assert cfg.concurrency == 2
    assert _by_name(cfg)["concurrency"].source == "flag:--concurrency"


def test_concurrency_falls_back_to_builtin_default(tmp_path):
    cfg = _cfg(load_settings(start_dir=tmp_path, env={}), _args())
    assert cfg.concurrency == 6
    assert _by_name(cfg)["concurrency"].source == "default"


def test_an_unset_param_names_the_fallback_that_takes_over(tmp_path):
    """`(unset)` alone hides how the run was configured; the table must say what
    happens instead."""
    cfg = _cfg(load_settings(start_dir=tmp_path, env={}), _args())
    rendered = {line.split("=")[0].strip(): line for line in cfg.render().splitlines()}

    assert f"(unset -> {UNSET_FALLBACK['base_branch']})" in rendered["base_branch"]
    assert f"(unset -> {UNSET_FALLBACK['pr']})" in rendered["pr"]


def test_the_fallback_note_is_display_only_and_never_reaches_the_trace(tmp_path):
    cfg = _cfg(load_settings(start_dir=tmp_path, env={}), _args())

    base = next(e for e in cfg.as_trace() if e["name"] == "base_branch")
    assert base == {"name": "base_branch", "value": None, "source": "cli"}


def test_the_flag_help_text_reuses_the_same_fallback_statement():
    """One statement of the fallback: the `--help` text and the effective-config
    table read the same string, so they cannot drift apart."""
    from roundtable import cli

    review_parser = cli.build_parser()._subparsers._group_actions[0].choices["review"]  # type: ignore[union-attr]
    helps = [a.help for a in review_parser._actions if a.dest == "base_branch"]
    assert helps and UNSET_FALLBACK["base_branch"] in helps[0]

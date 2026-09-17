"""``roundtable --version`` prints the resolved version and exits 0.

The top-level parser exposes the conventional ``--version`` flag so users can
confirm which build they have installed without invoking a subcommand. The
``version`` action short-circuits during parsing — before the required-subcommand
check — so it must succeed even though no command is supplied.
"""

from __future__ import annotations

import pytest

from roundtable import __version__, cli


def test_version_flag_prints_version_and_exits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"roundtable {__version__}"

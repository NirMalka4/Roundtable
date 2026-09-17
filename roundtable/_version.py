"""Single source of truth for the package version.

A release bumps ``__version__`` here and promotes the matching ``## [vX.Y.Z]``
heading in ``CHANGELOG.md`` in the *same* PR; CI's coherence gate
(``scripts/check_release_version.py``) fails the build if the two disagree. The
value below is the only place a version is authored.

``pyproject.toml`` reads this via ``[tool.setuptools.dynamic]`` so the built
wheel's metadata version equals this string.
"""

__version__ = "4.6.6"

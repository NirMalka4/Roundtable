"""Product-identity seam — the single place the product NAME is spelled.

This is distinct from :mod:`roundtable.bundle.paths` (which resolves the active
config *bundle* data root). Here we own the strings that name the running product
itself: the human/log-facing name, the ``~/<dir>`` runtime tree, and the lowercase
slug used for on-disk dotfiles. Every runtime artifact whose name would otherwise
hardcode "Roundtable" derives from one of these, so a rebrand edits three constants
in one file instead of chasing literals across the tree.

Values are intentionally unchanged from the historical hardcoded literals — this is
a pure centralization seam, not a rename.
"""

from __future__ import annotations

# Human/log-facing product name (e.g. the ``[Roundtable]`` stderr log prefix).
APP_NAME = "Roundtable"

# Runtime tree under the user's home: ``~/<APP_HOME_DIRNAME>/{artifacts,clones,...}``.
# Lowercase, matching the product/CLI name; a first-run migration moves a legacy
# ``~/InspectorX-py`` tree here (see :mod:`roundtable.bundle.runtime_home`).  # rebrand-compat
APP_HOME_DIRNAME = "roundtable"

# Lowercase slug for on-disk dotfiles/dotdirs (e.g. ``.roundtable-leases``).
APP_DOT_SLUG = "roundtable"

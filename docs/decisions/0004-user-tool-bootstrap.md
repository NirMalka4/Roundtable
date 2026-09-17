# ADR 0004: User-level tool bootstrap

**Status:** Accepted (2026-09-14)

The default bootstrap installs the current checkout as a user-level `uv` tool.
It updates the shell path and verifies both `roundtable` and `rt`, so users can
run either command from a new terminal without activating a repository
environment.

Contributor setup remains separate:
`python scripts/bootstrap.py --contributor` creates a checkout-local `.venv`
with development, lint, and type-checking extras.

**Consequences:** rerunning bootstrap replaces the existing user-level
Roundtable tool with the current checkout; contributor environments remain
local and independently managed. No package release index is built in.

**Rejected:** a checkout-local `.venv` as the default user installation because
it requires activation in every terminal.

# Installation

Roundtable requires Python 3.12 or newer.

## From a clone

The default bootstrap installs the current checkout as a user-level `uv` tool:

```text
python scripts/bootstrap.py
```

It checks for `uv` with the current Python. If necessary, it installs `uv` into
that Python's user environment, then invokes it consistently as `python -m uv`.
It replaces any existing Roundtable tool installation with the current checkout,
runs `uv tool update-shell`, locates the tool executable directory, and directly
verifies both `roundtable` and `rt`.

Open a new terminal if the commands are not visible in the shell that launched
the installer. Use `--no-update-shell` only when you intentionally manage PATH
yourself. `--dry-run` prints the flow without changing the machine.

Roundtable is installed from source. The project does not currently promise
installation from public PyPI.

## Contributor environment

Contributor mode is separate from the user-level tool installation:

```text
python scripts/bootstrap.py --contributor
```

It creates or reuses checkout-local `.venv` and installs the checkout with the
`dev`, `lint`, and `types` extras. `--force` recreates only a `.venv` carrying
the bootstrap ownership marker and refuses to delete an unrelated environment.
Activate that environment, or invoke its Python directly, for development.

## Workspace checkout timeout

Detached pull-request worktrees use a 600-second checkout timeout. Set
`ROUNDTABLE_WORKSPACE_CHECKOUT_TIMEOUT` or
`workspace.checkout_timeout_seconds` in `roundtable.yaml` when a large
repository needs longer. Values must be between 1 and 3600 seconds. A timeout is
reported as an explicit workspace failure and Roundtable removes or prunes any
partial worktree registration before a retry.

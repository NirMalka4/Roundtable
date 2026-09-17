# Contributing

## Propose a change

Fork the repository, create a focused branch from `main`, and open a pull
request back to `main`. Explain the change, its risk, and the checks you ran.
Keep generated artifacts, credentials, private endpoints, and private
repository content out of the contribution.

Record durable architecture decisions under
[`docs/decisions/`](docs/decisions/). For package boundaries and extension
contracts, follow [`AGENTS.md`](AGENTS.md) and
[`docs/architecture.md`](docs/architecture.md).

## Local setup

Roundtable requires Python 3.12 or newer. GitHub CI covers Python 3.12 and 3.13.
Create or reuse the checkout-local contributor environment:

```text
python scripts/bootstrap.py --contributor
```

This installs the runtime, test, lint, and type-check dependencies into `.venv`.
Activate it before running contributor commands, or invoke its Python directly.
Use `--force` only to recreate an environment previously created by the script.

Dependencies are declared in `pyproject.toml`. New or changed dependencies
require dependency and license review before release.

## Test the change

Start with the smallest test that covers the behavior, for example:

```text
python -m pytest tests/unit/inputs/test_replay.py -q
```

Tests must not perform live reviews, publication, adoption, evaluation, or
other external-state mutation. Use the deterministic mock backend, fake
adapters, and temporary directories.

Before opening or updating a pull request, run the authoritative gate set:

```text
python scripts/gates.py
```

It covers release coherence, package and documentation boundaries, lint,
formatting, types, the test suite with its branch-coverage floor, and static
bundle checks. GitHub CI delegates to the same script on Python 3.12 and 3.13.

## Contribution license

By submitting a contribution, you agree that it is licensed under the
repository's [MIT License](LICENSE).

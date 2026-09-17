"""Fresh-process runner for artifact operations whose session uses another bundle."""

from __future__ import annotations

import json
import os
import sys

from roundtable.bundle import ENV_VAR
from roundtable.graph import ConfigurationIdentityError, resolve_session_configuration


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] not in {"publish", "unpublish"}:
        print("artifact worker: expected OPERATION SESSION OPTIONS_JSON", file=sys.stderr)
        return 2
    operation, session_dir, raw_options = args
    try:
        identity = resolve_session_configuration(session_dir)
        options = json.loads(raw_options)
    except (ConfigurationIdentityError, ValueError) as err:
        print(f"[{operation}] BLOCKED — {err}", file=sys.stderr)
        return 2

    os.environ[ENV_VAR] = str(identity.root)
    from roundtable.ado import PublishOptions, UnpublishOptions

    from .cli import _run_publish_for_session, _run_unpublish_for_session

    if operation == "publish":
        return _run_publish_for_session(session_dir, PublishOptions(**options))
    return _run_unpublish_for_session(session_dir, UnpublishOptions(**options))


if __name__ == "__main__":
    raise SystemExit(main())

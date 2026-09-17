"""Run one frozen input through Red-Green without scheduling the shipped graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 frozen input file")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="workspace Red-Green may read")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the immutable request identity without starting the SDK",
    )
    return parser


def _frozen_input(path: Path) -> tuple[str, dict[str, object]]:
    payload_path = path / "source-payloads.json" if path.is_dir() else path
    raw = payload_path.read_bytes()
    text = raw.decode("utf-8")
    if payload_path.suffix.lower() == ".json":
        payloads = json.loads(text)
        if not isinstance(payloads, dict) or not isinstance(payloads.get("ReviewDiff"), str):
            raise ValueError("frozen JSON must contain a string ReviewDiff payload")
        text = payloads["ReviewDiff"]
    if not text.strip():
        raise ValueError("frozen input must not be empty")
    context_raw = text.encode("utf-8")
    return text, {
        "input": str(payload_path.resolve()),
        "inputSha256": hashlib.sha256(raw).hexdigest(),
        "inputBytes": len(raw),
        "contextSha256": hashlib.sha256(context_raw).hexdigest(),
        "contextBytes": len(context_raw),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    text, identity = _frozen_input(args.input)

    from roundtable.backend import BackendOptions, sdk_backend
    from roundtable.bundle import CONFIGS_DIR
    from roundtable.engine import run_agent_with_ovg
    from roundtable.graph import get_configuration, register_config_plugins

    bundle = (CONFIGS_DIR / "buddies").resolve()
    configuration = get_configuration(bundle)
    register_config_plugins(configuration)
    entry = configuration.by_key.get("redgreen")
    if entry is None:
        raise RuntimeError("buddies configuration has no redgreen entry")
    request = {
        **identity,
        "config": str(bundle),
        "agent": entry.key,
        "model": entry.model,
        "timeoutSeconds": entry.timeout_seconds or 600,
    }
    if args.dry_run:
        print(json.dumps(request, sort_keys=True))
        return 0

    cwd = str(args.cwd.resolve())
    with sdk_backend(BackendOptions(configuration=configuration, cwd=cwd)) as backend:
        outcome = run_agent_with_ovg(
            agent=entry.key,
            context=text,
            model=entry.model or "",
            backend=backend,
            configuration=configuration,
            timeout_s=request["timeoutSeconds"],
            cwd=cwd,
        )
    print(
        json.dumps(
            {
                **request,
                "valid": outcome.valid,
                "attempts": outcome.attempts,
                "lastError": outcome.last_error,
                "response": outcome.response,
            },
            ensure_ascii=False,
        )
    )
    return 0 if outcome.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())

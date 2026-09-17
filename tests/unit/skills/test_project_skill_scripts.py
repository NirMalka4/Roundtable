from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from roundtable import cli
from roundtable.engine import Engine
from roundtable.graph import Configuration

_ROOT = Path(__file__).resolve().parents[3]
_SKILLS = _ROOT / ".github" / "skills"


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SKILLS / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(relative: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SKILLS / relative), *args],
        cwd=_ROOT,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("script", "expected_key"),
    [
        ("roundtable-add-agent/scripts/inspect_bundle.py", "nodes"),
        ("roundtable-manage-mcp/scripts/inspect_mcp.py", "servers"),
    ],
)
def test_inspection_scripts_emit_structured_current_bundle_evidence(script, expected_key):
    result = _run(script, "--config", "buddies", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["configuration"]["name"] == "buddies"
    assert expected_key in payload


def test_inspect_bundle_reports_unknown_agent_as_a_usage_error():
    result = _run(
        "roundtable-add-agent/scripts/inspect_bundle.py",
        "--config",
        "buddies",
        "--agent",
        "not-a-node",
        "--json",
    )

    assert result.returncode == 2
    assert "unknown agent 'not-a-node'" in result.stderr


@pytest.mark.parametrize(
    ("script", "config_text", "message"),
    [
        (
            "roundtable-add-agent/scripts/inspect_bundle.py",
            "agents:\n- key: Input\n  emoji: I\n  unexpected: true\n",
            "unexpected",
        ),
        (
            "roundtable-add-agent/scripts/inspect_bundle.py",
            "plugins: [module.that.does.not.exist]\nagents:\n- key: Input\n  kind: source\n  emoji: I\n",
            "No module named",
        ),
        (
            "roundtable-manage-mcp/scripts/inspect_mcp.py",
            "agents: not-a-list\n",
            "expected type array, got string",
        ),
    ],
)
def test_inspection_scripts_report_malformed_configs_and_plugin_failures(
    tmp_path, script, config_text, message
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "agent_graph.yaml").write_text(config_text, encoding="utf-8")

    result = _run(script, "--config", str(bundle), "--json")

    assert result.returncode == 2
    assert message in result.stderr


def test_validate_mcp_accepts_the_shipped_buddies_contract():
    result = _run(
        "roundtable-manage-mcp/scripts/validate_mcp.py",
        "--config",
        "buddies",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_validate_mcp_runs_doctor_in_static_mode(monkeypatch):
    module = _load("validate_mcp_static_script", "roundtable-manage-mcp/scripts/validate_mcp.py")
    parsed_args = []

    def capture_doctor(command, **_kwargs):
        parsed_args.append(cli.build_parser().parse_args(command[3:]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", capture_doctor)

    assert module.validate("buddies")["ok"] is True
    assert len(parsed_args) == 1
    assert parsed_args[0].static is True


def test_validate_mcp_rejects_provisioning_without_explicit_permission(tmp_path):
    module = _load("validate_mcp_script", "roundtable-manage-mcp/scripts/validate_mcp.py")
    config = Configuration.from_document(
        {
            "agents": [
                {"key": "Input", "kind": "source", "emoji": "I"},
                {
                    "key": "Consumer",
                    "emoji": "C",
                    "agent_id": "consumer",
                    "display_name": "Consumer",
                    "edges": [{"from": "Input"}],
                    "system_prompt": {"instructions": "Agents/consumer.agent.md"},
                    "model": "gpt-5.6-sol",
                    "tools": ["view"],
                    "mcp": [{"server": "ado-code-read"}],
                    "output_schema": "consumer.schema.yaml",
                    "ovg_gates": [{"gate": "json_schema"}],
                },
            ]
        },
        root=tmp_path,
    )

    assert module._binding_errors(config) == [
        "Consumer: provisions MCP server 'ado-code-read' but grants none of its tools in the "
        "explicit `tools` allowlist"
    ]


def _scaffold_spec(root: Path) -> dict[str, object]:
    return {
        "root": str(root),
        "name": "generated",
        "executor": "dag",
        "sink": None,
        "sink_cardinality": "one",
        "source": {"key": "Input", "emoji": "I"},
        "node": {
            "key": "Result",
            "kind": "llm",
            "emoji": "R",
            "agent_id": "result",
            "display_name": "Result",
            "description": "Returns one deterministic result.",
            "prompt_body": "# Result\n\nUse the supplied input.",
            "model": "gpt-5.6-sol",
            "tools": [],
            "output_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {"value": {"type": "string", "description": "The domain result."}},
                "examples": [{"value": "example"}],
            },
            "ovg_gates": [{"gate": "json_schema"}],
        },
    }


def test_scaffold_previews_then_writes_the_same_valid_configuration(tmp_path):
    module = _load("scaffold_config_script", "roundtable-add-config/scripts/scaffold_config.py")
    root = tmp_path / "bundle"
    spec = _scaffold_spec(root)

    preview = module.scaffold(spec, write=False)
    written = module.scaffold(
        spec,
        write=True,
        doctor_runner=module._doctor,
    )
    loaded = Configuration.from_file(root)

    assert preview["status"] == "preview"
    assert written["status"] == "written"
    assert loaded.name == "generated"
    assert loaded.domain_values is None
    assert [entry.key for entry in loaded.entries] == ["Input", "Result"]
    assert (root / "prompts" / "Reviewer" / "Agents" / "Result.agent.md").is_file()
    assert not (root / "enums.yaml").exists()
    assert "agent graph OK" in written["doctor"]

    result = Engine("mock").run(loaded, {"Input": "generic input"})

    assert result.results["Result"].valid is True
    assert json.loads(result.results["Result"].response) == {"value": "example"}

    review = subprocess.run(
        [
            sys.executable,
            "-m",
            "roundtable.cli",
            "review",
            str(tmp_path),
            "--config",
            str(root),
            "--simulate",
        ],
        cwd=_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert review.returncode == 4
    assert "roundtable review requires `domain_values` for severity and verdict" in review.stderr


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda spec: spec.pop("name"), "spec.name must be a non-empty string"),
        (
            lambda spec: spec["node"].update(kind="not-registered"),
            "spec.node.kind 'not-registered' is unsupported",
        ),
        (
            lambda spec: spec.update(plugins=["generated.plugins"]),
            "referenced plugin module has no existing file",
        ),
        (
            lambda spec: spec["node"].update(key="../../escape"),
            "generated path escapes configuration root",
        ),
    ],
)
def test_scaffold_rejects_malformed_unsupported_and_missing_references(tmp_path, mutate, message):
    module = _load(
        f"scaffold_config_error_{message[:4]}",
        "roundtable-add-config/scripts/scaffold_config.py",
    )
    spec = _scaffold_spec(tmp_path / "bundle")
    mutate(spec)

    with pytest.raises((ValueError, FileNotFoundError), match=message):
        module.scaffold(spec, write=False)


def test_scaffold_preserves_optional_run_scoped_domain_values(tmp_path):
    module = _load(
        "scaffold_config_domain_values",
        "roundtable-add-config/scripts/scaffold_config.py",
    )
    spec = _scaffold_spec(tmp_path / "bundle")
    spec["domain_values"] = {"values": {"priority": ["routine", "urgent"]}}

    preview = module.scaffold(spec, write=False)
    configuration = Configuration.from_document(
        preview["configuration"],
        root=tmp_path / "bundle",
    )

    assert configuration.domain_values is not None
    assert configuration.domain_values.get("priority") == ("routine", "urgent")


def test_scaffold_preserves_optional_publishing_policy(tmp_path):
    module = _load(
        "scaffold_config_publishing",
        "roundtable-add-config/scripts/scaffold_config.py",
    )
    spec = _scaffold_spec(tmp_path / "bundle")
    spec["domain_values"] = {"values": {"severity": ["notice", "danger"]}}
    spec["publishing"] = {"default_min_severity": "notice"}

    preview = module.scaffold(spec, write=False)
    configuration = Configuration.from_document(
        preview["configuration"],
        root=tmp_path / "bundle",
    )

    assert configuration.publishing.default_min_severity == "notice"


def test_scaffold_refuses_overwrite_and_rolls_back_doctor_failure(tmp_path):
    module = _load(
        "scaffold_config_rollback",
        "roundtable-add-config/scripts/scaffold_config.py",
    )
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "agent_graph.yaml").write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        module.scaffold(_scaffold_spec(occupied), write=False)

    failed = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="doctor failed"):
        module.scaffold(
            _scaffold_spec(failed),
            write=True,
            doctor_runner=lambda _path: (1, "deliberate failure"),
        )
    assert not any(path.is_file() for path in failed.rglob("*"))


# ── standalone invocability ─────────────────────────────────────────────────
def _scripts_importing_roundtable() -> list[Path]:
    return sorted(
        path
        for path in _SKILLS.rglob("scripts/*.py")
        if "roundtable." in path.read_text(encoding="utf-8")
    )


def test_every_skill_script_can_import_roundtable_when_run_directly():
    """A skill script is launched by path, so its own directory — not the repo root —
    lands on ``sys.path``. Each one that imports the package must therefore put the
    root on the path itself; ``plan_review.py`` once did not and died on import.

    A usage error is a pass: argparse only runs once the imports have succeeded.
    """
    scripts = _scripts_importing_roundtable()
    assert scripts, "expected at least one skill script importing roundtable"

    unimportable = {}
    for script in scripts:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=_ROOT,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if "No module named 'roundtable'" in result.stderr:
            unimportable[script.relative_to(_SKILLS).as_posix()] = result.stderr.strip()
    assert unimportable == {}

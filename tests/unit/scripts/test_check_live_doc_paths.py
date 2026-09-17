from __future__ import annotations

from scripts.check_live_doc_paths import check


def test_live_path_check_scans_root_and_project_skills_but_not_history(tmp_path) -> None:
    skill = tmp_path / ".github" / "skills" / "example"
    skill.mkdir(parents=True)
    (tmp_path / "roundtable").mkdir()
    (tmp_path / "roundtable" / "live.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "`roundtable/live.py`\n`roundtable/missing.py`\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("", encoding="utf-8")
    (tmp_path / "ARCHIVE.md").write_text("`roundtable/retired.py`", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "`roundtable/live.py`\n`roundtable/skill-missing.yaml`\n",
        encoding="utf-8",
    )

    assert check(tmp_path) == [
        "README.md:2: missing cited path roundtable/missing.py",
        ".github/skills/example/SKILL.md:2: missing cited path roundtable/skill-missing.yaml",
    ]

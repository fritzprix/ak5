"""``ak5 skill show|install`` and the bundled skill copy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ak5.cli.commands.skill import bundled_skill_files
from ak5.cli.main import cli
from click.testing import CliRunner

REPO_SKILL = Path(__file__).resolve().parents[2] / "skills" / "ak5"


def _normalized(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def test_bundled_skill_matches_repo_skill() -> None:
    repo = {
        p.relative_to(REPO_SKILL).as_posix(): _normalized(p.read_bytes())
        for p in REPO_SKILL.rglob("*")
        if p.is_file()
    }
    bundled = {f.relpath.as_posix(): _normalized(f.data) for f in bundled_skill_files()}
    assert bundled == repo, "copy skills/ak5/ to backend/src/ak5/skills/ak5/"


def test_skill_show_prints_skill_md() -> None:
    result = CliRunner().invoke(cli, ["skill", "show"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("---\nname: ak5")


def test_skill_install_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    skill_md = tmp_path / ".claude" / "skills" / "ak5" / "SKILL.md"

    first = runner.invoke(cli, ["--json", "skill", "install", "--harness", "claude"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["results"][0]["status"] == "installed"
    assert skill_md.is_file()
    assert (skill_md.parent / "examples" / "mcp_config.json").is_file()

    again = runner.invoke(cli, ["--json", "skill", "install", "--harness", "claude"])
    assert json.loads(again.output)["results"][0]["status"] == "unchanged"

    skill_md.write_text("local edit", encoding="utf-8")
    skipped = runner.invoke(cli, ["--json", "skill", "install", "--harness", "claude"])
    assert skipped.exit_code == 3
    assert json.loads(skipped.output)["results"][0]["status"] == "skipped"
    assert skill_md.read_text(encoding="utf-8") == "local edit"

    forced = runner.invoke(cli, ["--json", "skill", "install", "--harness", "claude", "--force"])
    assert forced.exit_code == 0, forced.output
    assert json.loads(forced.output)["results"][0]["status"] == "updated"
    assert skill_md.read_text(encoding="utf-8").startswith("---")


def test_skill_install_defaults_to_agents_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["skill", "install"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".agents" / "skills" / "ak5" / "SKILL.md").is_file()


def test_skill_install_global_and_custom_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    custom = tmp_path / "custom"
    result = CliRunner().invoke(
        cli, ["skill", "install", "--global", "--harness", "cursor", "--dir", str(custom)]
    )
    assert result.exit_code == 0, result.output
    assert (home / ".cursor" / "skills" / "ak5" / "SKILL.md").is_file()
    assert (custom / "ak5" / "SKILL.md").is_file()

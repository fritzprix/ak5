"""``scripts/vercel_deploy.py``: Python-only Vercel stage (#179 section B)."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from vercel_deploy import (  # noqa: E402
    WHEEL_CHUNKS_PREFIX,
    DeployError,
    find_project_link,
    parse_deployment_url,
    stage,
    verify_wheel,
    web_ui_built,
)

WHEEL_NAME = "ak5-2.0.0-py3-none-any.whl"


def _wheel(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return path


def test_verify_wheel_accepts_chunks(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / WHEEL_NAME, ["ak5/__init__.py", f"{WHEEL_CHUNKS_PREFIX}webpack-abc.js"])
    verify_wheel(wheel)


def test_verify_wheel_rejects_missing_chunks(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / WHEEL_NAME, ["ak5/__init__.py", "ak5/server/web_ui/index.html"])
    with pytest.raises(DeployError, match="dashboard would 404"):
        verify_wheel(wheel)


def test_verify_wheel_rejects_duplicate_paths(tmp_path: Path) -> None:
    wheel = tmp_path / WHEEL_NAME
    with zipfile.ZipFile(wheel, "w") as zf, pytest.warns(UserWarning, match="Duplicate name"):
        zf.writestr("ak5/server/web_ui/.keep", "")
        zf.writestr("ak5/server/web_ui/.keep", "")
        zf.writestr(f"{WHEEL_CHUNKS_PREFIX}main.js", "x")
    with pytest.raises(DeployError, match="duplicate paths"):
        verify_wheel(wheel)


def test_web_ui_built(tmp_path: Path) -> None:
    chunks = tmp_path / "chunks"
    assert not web_ui_built(chunks)
    chunks.mkdir()
    assert not web_ui_built(chunks)
    (chunks / "main-app-1.js").write_text("x", encoding="utf-8")
    assert web_ui_built(chunks)


def test_stage_is_python_only(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / WHEEL_NAME, [f"{WHEEL_CHUNKS_PREFIX}main.js"])
    link = tmp_path / "project.json"
    link.write_text('{"orgId":"o","projectId":"p"}', encoding="utf-8")
    stage_dir = tmp_path / "stage"
    (stage_dir / "stale").mkdir(parents=True)

    stage(stage_dir, wheel, link)

    assert sorted(p.relative_to(stage_dir).as_posix() for p in stage_dir.rglob("*") if p.is_file()) == [
        ".vercel/project.json",
        WHEEL_NAME,
        "api/index.py",
        "requirements.txt",
        "vercel.json",
    ]
    assert (stage_dir / "requirements.txt").read_text(encoding="utf-8") == f"./{WHEEL_NAME}[server,postgres,vercel]\n"
    config = json.loads((stage_dir / "vercel.json").read_text(encoding="utf-8"))
    assert config["builds"] == [{"src": "api/index.py", "use": "@vercel/python"}]


def test_stage_without_project_link(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / WHEEL_NAME, [f"{WHEEL_CHUNKS_PREFIX}main.js"])
    stage(tmp_path / "stage", wheel, None)
    assert not (tmp_path / "stage" / ".vercel").exists()


def test_find_project_link(tmp_path: Path) -> None:
    assert find_project_link(tmp_path) is None
    (tmp_path / ".vercel").mkdir()
    (tmp_path / ".vercel" / "project.json").write_text("{}", encoding="utf-8")
    assert find_project_link(tmp_path) == tmp_path / ".vercel" / "project.json"


def test_parse_deployment_url_takes_last() -> None:
    out = "Inspect: https://vercel.com/team/ak5/abc\nhttps://ak5-abc123-team.vercel.app\n"
    assert parse_deployment_url(out) == "https://ak5-abc123-team.vercel.app"


def test_parse_deployment_url_missing() -> None:
    with pytest.raises(DeployError, match="no deployment URL"):
        parse_deployment_url("Error: something went wrong")

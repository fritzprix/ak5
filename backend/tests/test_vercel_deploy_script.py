"""``scripts/vercel_deploy.py``: Python-only Vercel stage (#179 section B)."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import vercel_deploy  # noqa: E402
from smoke_hosted import CheckResult  # noqa: E402
from vercel_deploy import (  # noqa: E402
    STAGE_MARKER,
    WHEEL_CHUNKS_PREFIX,
    DeployError,
    check_stage_dir,
    find_project_link,
    main,
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

    stage(stage_dir, wheel, link, root=tmp_path / "repo")
    (stage_dir / "stale.txt").write_text("from an earlier run", encoding="utf-8")
    stage(stage_dir, wheel, link, root=tmp_path / "repo")

    assert sorted(p.relative_to(stage_dir).as_posix() for p in stage_dir.rglob("*") if p.is_file()) == [
        STAGE_MARKER,
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
    stage(tmp_path / "stage", wheel, None, root=tmp_path / "repo")
    assert not (tmp_path / "stage" / ".vercel").exists()


@pytest.mark.parametrize("relative", [".", ".."])
def test_check_stage_dir_refuses_repo_and_ancestors(tmp_path: Path, relative: str) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(DeployError, match="contains the repository"):
        check_stage_dir(repo / relative, repo)
    assert (repo / ".git").is_dir()


def test_check_stage_dir_refuses_foreign_non_empty_dir(tmp_path: Path) -> None:
    build = tmp_path / "build"
    build.mkdir()
    (build / "keep.txt").write_text("unrelated", encoding="utf-8")
    wheel = _wheel(tmp_path / WHEEL_NAME, [f"{WHEEL_CHUNKS_PREFIX}main.js"])
    with pytest.raises(DeployError, match="not a previous stage"):
        stage(build, wheel, None, root=tmp_path / "repo")
    assert (build / "keep.txt").is_file()


def test_check_stage_dir_refuses_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(DeployError, match="not a directory"):
        check_stage_dir(target, tmp_path / "repo")


def test_check_stage_dir_accepts_missing_and_empty(tmp_path: Path) -> None:
    check_stage_dir(tmp_path / "missing", tmp_path / "repo")
    (tmp_path / "empty").mkdir()
    check_stage_dir(tmp_path / "empty", tmp_path / "repo")


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


DEPLOY_URL = "https://ak5-abc123-team.vercel.app"
PASS = [CheckResult("readyz", True, "ready")]
FAIL = [CheckResult("readyz", False, "HTTP 503: starting")]


class FakeDeploy:
    """Stubs the build, the ``vercel`` subprocess, and the smoke checks for ``main()``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.vercel_calls: list[list[str]] = []
        self.vercel_returncode = 0
        self.smoke_results: list[list[CheckResult]] = [PASS]
        self.smoke_urls: list[str] = []
        link = tmp_path / "project.json"
        link.write_text('{"orgId":"o","projectId":"p"}', encoding="utf-8")
        self.project_link: Path | None = link
        self.web_ui = True

        monkeypatch.delenv("VERCEL_ORG_ID", raising=False)
        monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)
        monkeypatch.setattr(vercel_deploy, "web_ui_built", lambda: self.web_ui)
        monkeypatch.setattr(vercel_deploy, "find_project_link", lambda: self.project_link)
        monkeypatch.setattr(vercel_deploy, "build_wheel", self._build_wheel)
        monkeypatch.setattr(vercel_deploy, "vercel_command", lambda: ["vercel"])
        monkeypatch.setattr(vercel_deploy.subprocess, "run", self._run)
        monkeypatch.setattr(vercel_deploy, "run_checks", self._run_checks)
        monkeypatch.setattr(vercel_deploy.time, "sleep", lambda _seconds: None)

    def _build_wheel(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True)
        return _wheel(out_dir / WHEEL_NAME, [f"{WHEEL_CHUNKS_PREFIX}main.js"])

    def _run(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.vercel_calls.append(cmd)
        stdout = f"{DEPLOY_URL}\n" if self.vercel_returncode == 0 else ""
        return subprocess.CompletedProcess(cmd, self.vercel_returncode, stdout=stdout)

    def _run_checks(self, url: str) -> list[CheckResult]:
        self.smoke_urls.append(url)
        return self.smoke_results.pop(0) if len(self.smoke_results) > 1 else self.smoke_results[0]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeDeploy:
    return FakeDeploy(monkeypatch, tmp_path)


def test_main_refuses_without_dashboard_chunks(fake: FakeDeploy, capsys: pytest.CaptureFixture[str]) -> None:
    fake.web_ui = False
    assert main([]) == 1
    assert "build_web_ui.sh" in capsys.readouterr().err
    assert fake.vercel_calls == []


def test_main_refuses_without_project_link(fake: FakeDeploy, capsys: pytest.CaptureFixture[str]) -> None:
    fake.project_link = None
    assert main([]) == 1
    assert "vercel link" in capsys.readouterr().err
    assert fake.vercel_calls == []


def test_main_accepts_org_and_project_env(fake: FakeDeploy, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.project_link = None
    monkeypatch.setenv("VERCEL_ORG_ID", "o")
    monkeypatch.setenv("VERCEL_PROJECT_ID", "p")
    assert main([]) == 0


def test_main_deploys_production_by_default(fake: FakeDeploy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "secret-token")
    assert main([]) == 0
    assert fake.vercel_calls == [["vercel", "deploy", "--yes", "--prod"]]
    assert fake.smoke_urls == [DEPLOY_URL]


def test_main_preview_omits_prod(fake: FakeDeploy) -> None:
    assert main(["--preview"]) == 0
    assert fake.vercel_calls == [["vercel", "deploy", "--yes"]]


def test_main_vercel_failure_returns_1(fake: FakeDeploy, capsys: pytest.CaptureFixture[str]) -> None:
    fake.vercel_returncode = 2
    assert main([]) == 1
    assert "vercel deploy exited 2" in capsys.readouterr().err
    assert fake.smoke_urls == []


def test_main_smoke_retries_then_passes(fake: FakeDeploy) -> None:
    fake.smoke_results = [FAIL, PASS]
    assert main(["--smoke-url", "https://ak5.example"]) == 0
    assert fake.smoke_urls == ["https://ak5.example", "https://ak5.example"]


def test_main_smoke_failure_returns_1(fake: FakeDeploy, capsys: pytest.CaptureFixture[str]) -> None:
    fake.smoke_results = [FAIL]
    assert main([]) == 1
    assert len(fake.smoke_urls) == 3
    assert f"deploy succeeded ({DEPLOY_URL})" in capsys.readouterr().err


def test_main_no_smoke(fake: FakeDeploy) -> None:
    assert main(["--no-smoke"]) == 0
    assert fake.smoke_urls == []


def test_main_stage_only_requires_stage_dir(fake: FakeDeploy) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--stage-only"])
    assert exc.value.code == 2


def test_main_stage_only_keeps_stage(fake: FakeDeploy) -> None:
    stage_dir = fake.tmp_path / "out"
    assert main(["--stage-only", "--stage-dir", str(stage_dir)]) == 0
    assert (stage_dir / STAGE_MARKER).is_file()
    assert fake.vercel_calls == []


def test_main_refuses_repo_root_stage_dir(fake: FakeDeploy, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--stage-only", "--stage-dir", str(vercel_deploy.ROOT)]) == 1
    assert "contains the repository" in capsys.readouterr().err
    assert (vercel_deploy.ROOT / "scripts" / "vercel_deploy.py").is_file()

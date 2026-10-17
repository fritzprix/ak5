"""Base-install CLI must work without sqlalchemy / server extra (#18)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


def _run_isolated(code: str, *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run Python with sqlalchemy blocked so accidental server imports fail loudly."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    blocker = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'sqlalchemy' or name.startswith('sqlalchemy.'):\n"
        "            raise ModuleNotFoundError(name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
    )
    return subprocess.run(
        [sys.executable, "-c", blocker + code],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(SRC.parents[1]),
        env=env,
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["next", "--help"],
        ["ticket", "--help"],
        ["daemon", "--help"],
        ["subscribe", "--help"],
        ["profile", "--help"],
    ],
)
def test_client_cli_help_without_sqlalchemy(argv: list[str]) -> None:
    code = (
        "from click.testing import CliRunner\n"
        "from ak5.cli.main import cli\n"
        f"r = CliRunner().invoke(cli, {argv!r})\n"
        "assert r.exit_code == 0, r.output\n"
        "print('ok')\n"
    )
    proc = _run_isolated(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


def test_serve_hints_server_extra_without_sqlalchemy() -> None:
    code = (
        "from click.testing import CliRunner\n"
        "from ak5.cli.main import cli\n"
        "r = CliRunner().invoke(cli, ['serve', '--help'])\n"
        # --help must still work (lazy guard runs on invoke of serve body)
        "assert r.exit_code == 0, r.output\n"
        "r2 = CliRunner().invoke(cli, ['serve', '--host', '127.0.0.1', '--port', '9'])\n"
        "assert r2.exit_code != 0\n"
        "out = (r2.output or '') + (r2.stderr or '')\n"
        "assert 'ak5[server]' in out or 'server' in out.lower()\n"
        "print('ok')\n"
    )
    proc = _run_isolated(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr

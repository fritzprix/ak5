"""Regression: base install messaging for ``ak5[server]`` (issue #18)."""

from __future__ import annotations

import builtins

import pytest
from ak5.cli.main import cli
from ak5.cli.server_extra import SERVER_INSTALL_HINT, require_server_extra, server_extra_available
from click.testing import CliRunner


def test_require_server_extra_prints_hint_when_deps_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name in {"sqlalchemy", "aiosqlite", "greenlet", "sse_starlette"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as exc_info:
        require_server_extra()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "ak5[server]" in err
    assert "pip install" in err
    assert SERVER_INSTALL_HINT.splitlines()[0] in err


def test_server_extra_available_matches_imports() -> None:
    # Dev/CI installs ``[server]``; this asserts the probe stays consistent.
    assert server_extra_available() is True


def test_serve_command_exits_when_server_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import ak5.cli.server_extra as server_extra_mod

    def _boom() -> None:
        print(SERVER_INSTALL_HINT, flush=True)
        raise SystemExit(1)

    # ``run_serve`` does a local ``from ak5.cli.server_extra import require_server_extra``.
    monkeypatch.setattr(server_extra_mod, "require_server_extra", _boom)

    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--host", "127.0.0.1", "--port", "9"])
    assert result.exit_code == 1
    combined = (result.output or "") + (getattr(result, "stderr", None) or "")
    assert "ak5[server]" in combined

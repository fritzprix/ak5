"""Tests for server deployment profile resolution and AK5_PROFILE separation (#156).

Verifies:
- AK5_DEPLOY_PROFILE=hosted -> hosted deployment profile.
- AK5_DEPLOY_PROFILE with unknown values refuse (fail-closed).
- AK5_PROFILE=worker-1 (CLI profile) -> local server deployment profile without interference.
- AK5_PROFILE=hosted (legacy fallback) -> hosted + one-time logger.warning.
- Server code has exactly one os.environ.get("AK5_PROFILE") call for deprecated fallback.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
import ak5.server.config as config_mod
from ak5.server.config import get_deploy_profile, hosted_profile


@pytest.fixture(autouse=True)
def _reset_legacy_warning_flag() -> None:
    config_mod._legacy_deploy_profile_warned = False
    yield
    config_mod._legacy_deploy_profile_warned = False


def test_default_deploy_profile_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    assert get_deploy_profile() == "local"
    assert hosted_profile() is False


def test_deploy_profile_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hosted")
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    assert get_deploy_profile() == "hosted"
    assert hosted_profile() is True


def test_deploy_profile_precedence_over_legacy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "local")
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    with caplog.at_level(logging.WARNING, logger="ak5.server.config"):
        assert get_deploy_profile() == "local"
        assert hosted_profile() is False
    assert not any("deprecated" in r.message for r in caplog.records)


def test_cli_profile_worker_defaults_to_local(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)
    monkeypatch.setenv("AK5_PROFILE", "worker-1")
    with caplog.at_level(logging.WARNING, logger="ak5.server.config"):
        assert get_deploy_profile() == "local"
        assert hosted_profile() is False
    assert not any("deprecated" in r.message for r in caplog.records)


def test_invalid_deploy_profile_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "hostd")
    monkeypatch.delenv("AK5_PROFILE", raising=False)
    with pytest.raises(ValueError, match="Invalid AK5_DEPLOY_PROFILE='hostd'.*Allowed values"):
        get_deploy_profile()


@pytest.mark.parametrize("bad", ["prod", "Hostd", " staging "])
def test_invalid_deploy_profile_variants_are_rejected(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", bad)
    with pytest.raises(ValueError, match="Invalid AK5_DEPLOY_PROFILE"):
        get_deploy_profile()


def test_legacy_profile_hosted_logs_warning_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    with caplog.at_level(logging.WARNING, logger="ak5.server.config"):
        assert get_deploy_profile() == "hosted"
        assert hosted_profile() is True
        assert get_deploy_profile() == "hosted"
    messages = [r.message for r in caplog.records if "deprecated" in r.message]
    assert len(messages) == 1
    assert "AK5_DEPLOY_PROFILE" in messages[0]


def test_legacy_profile_local_logs_warning_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)
    monkeypatch.setenv("AK5_PROFILE", "local")
    with caplog.at_level(logging.WARNING, logger="ak5.server.config"):
        assert get_deploy_profile() == "local"
        assert hosted_profile() is False
        assert get_deploy_profile() == "local"
    messages = [r.message for r in caplog.records if "deprecated" in r.message]
    assert len(messages) == 1


def test_only_one_environ_get_ak5_profile_in_server_code() -> None:
    """Issue #156 acceptance criteria:

    Only one os.environ.get("AK5_PROFILE") call exists in backend/src/ak5/server (the deprecated fallback).
    """
    server_dir = Path(__file__).resolve().parent.parent / "src" / "ak5" / "server"
    matches: list[tuple[str, int, str]] = []
    pattern = re.compile(r'os\.environ\.get\(\s*["\']AK5_PROFILE["\']\s*\)')

    for py_file in server_dir.glob("**/*.py"):
        lines = py_file.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, start=1):
            if pattern.search(line):
                matches.append((py_file.name, idx, line.strip()))

    assert len(matches) == 1, f"Expected exactly 1 os.environ.get('AK5_PROFILE') in server, found: {matches}"
    assert matches[0][0] == "config.py", f"Expected match in config.py, found: {matches[0]}"

"""Tests for server deployment profile resolution and AK5_PROFILE separation (#156).

Verifies:
- AK5_DEPLOY_PROFILE=hosted -> hosted deployment profile.
- AK5_PROFILE=worker-1 (CLI profile) -> local server deployment profile without interference.
- AK5_PROFILE=hosted (legacy fallback) -> hosted + DeprecationWarning.
- Server code has exactly one os.environ.get("AK5_PROFILE") call for deprecated fallback.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest
from ak5.server.config import get_deploy_profile, hosted_profile


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


def test_deploy_profile_precedence_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AK5_DEPLOY_PROFILE", "local")
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert get_deploy_profile() == "local"
        assert hosted_profile() is False
        assert len(recorded) == 0, "No warning when AK5_DEPLOY_PROFILE is explicitly set"


def test_cli_profile_worker_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)
    monkeypatch.setenv("AK5_PROFILE", "worker-1")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert get_deploy_profile() == "local"
        assert hosted_profile() is False
        assert len(recorded) == 0, "CLI profile name should not trigger warning or hosted mode"


def test_legacy_profile_hosted_deprecation_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)
    monkeypatch.setenv("AK5_PROFILE", "hosted")
    with pytest.deprecated_call(match="AK5_PROFILE='hosted' is deprecated.*AK5_DEPLOY_PROFILE"):
        assert get_deploy_profile() == "hosted"
        assert hosted_profile() is True


def test_legacy_profile_local_deprecation_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AK5_DEPLOY_PROFILE", raising=False)
    monkeypatch.setenv("AK5_PROFILE", "local")
    with pytest.deprecated_call(match="AK5_PROFILE='local' is deprecated.*AK5_DEPLOY_PROFILE"):
        assert get_deploy_profile() == "local"
        assert hosted_profile() is False


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
